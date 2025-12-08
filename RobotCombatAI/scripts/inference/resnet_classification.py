#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ResNet50图像分类推理脚本
用于机器人格斗竞赛中的场景理解

作者: RobotCombatAI团队
日期: 2025-12-08
"""

import os
import sys
import time
import argparse
import numpy as np
import cv2

# 设置环境变量
os.environ['ASCEND_TOOLKIT_HOME'] = '/usr/local/Ascend/ascend-toolkit/8.0.0'
os.environ['ASCEND_OPP_PATH'] = '/usr/local/Ascend/ascend-toolkit/8.0.0/opp'

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), '../../vision_perception/resnet_classification'))
sys.path.append(os.path.join(os.path.dirname(__file__), '../../vision_perception/acl_utils'))

try:
    import acl
    from label import label
except ImportError as e:
    print(f"❌ 导入错误: {e}")
    print("请确保在正确的环境中运行此脚本")
    sys.exit(1)

# 配置参数
RESNET_CONFIG = {
    'model_path': '../../models/om_models/resnet50.om',
    'input_size': (224, 224),
    'mean': [123.675, 116.28, 103.53],
    'std': [0.01712475, 0.017507, 0.017429]
}

class ResNet50Classifier:
    """ResNet50图像分类器"""

    def __init__(self, config=None):
        self.config = RESNET_CONFIG if config is None else config
        self.model_desc = None
        self.model_id = None
        self.context = None
        self.dataset = None
        self.labels = label  # 导入ImageNet标签

        self.init_model()

    def init_model(self):
        """初始化模型和ACL环境"""
        print("🎯 初始化ResNet50分类器...")

        # 初始化ACL
        acl.init()
        ret = acl.rt.set_device(0)
        if ret:
            raise RuntimeError(f"设置设备失败: {ret}")

        self.context, ret = acl.rt.create_context(0)
        if ret:
            raise RuntimeError(f"创建上下文失败: {ret}")

        # 加载模型
        print(f"📁 加载模型: {self.config['model_path']}")
        self.model_id, ret = acl.mdl.load_from_file(self.config['model_path'])
        if ret:
            raise RuntimeError(f"加载模型失败: {ret}")

        # 创建模型描述
        self.model_desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.model_desc, self.model_id)
        if ret:
            raise RuntimeError(f"获取模型描述失败: {ret}")

        # 创建输入数据集
        self.dataset = acl.mdl.create_dataset()
        if not self.dataset:
            raise RuntimeError("创建数据集失败")

        print("✅ ResNet50模型初始化完成")

    def preprocess_image(self, image_path):
        """图像预处理"""
        # 读取图片
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"无法读取图片: {image_path}")

        print(f"🖼️  原图尺寸: {img.shape}")

        # 缩放到模型输入尺寸
        img = cv2.resize(img, self.config['input_size'])

        # 归一化
        img = img.astype(np.float32)

        # 应用ImageNet标准化
        img = img.transpose(2, 0, 1)  # HWC to CHW
        img = img.copy()

        for i in range(3):
            img[i] = (img[i] / 255.0 - self.config['mean'][i]) / self.config['std'][i]

        return img

    def classify(self, image_path):
        """
        执行图像分类

        Args:
            image_path (str): 输入图片路径

        Returns:
            dict: 分类结果 {class_id, class_name, confidence}
        """
        print(f"🔍 分析图片: {os.path.basename(image_path)}")

        # 预处理
        start_time = time.time()
        img = self.preprocess_image(image_path)
        preprocess_time = time.time() - start_time

        # 准备输入
        input_buffer = acl.create_data_buffer(
            img.size * img.itemsize,
            acl.rt.malloc(img.size * img.itemsize),
            img.size * img.itemsize
        )
        ret = acl.rt.memcpy(
            input_buffer["buffer"], input_buffer["size"],
            img.ctypes.data, input_buffer["size"],
            1, 0
        )
        if ret:
            raise RuntimeError(f"数据拷贝失败: {ret}")

        # 添加输入到数据集
        ret = acl.mdl.add_dataset_buffer(self.dataset, input_buffer, False)
        if ret:
            raise RuntimeError(f"添加数据集缓冲区失败: {ret}")

        # 执行推理
        inference_start = time.time()
        ret = acl.mdl.execute(self.model_id, self.dataset, None)
        if ret:
            raise RuntimeError(f"模型推理失败: {ret}")
        inference_time = time.time() - inference_start

        # 获取输出
        output_data = []
        output_size = acl.mdl.get_dataset_num_buffers(self.dataset)
        for i in range(output_size):
            output_buffer = acl.mdl.get_dataset_buffer(self.dataset, i)
            output_data_buffer_addr = acl.get_data_buffer_addr(output_buffer)
            output_data_size = acl.get_data_buffer_size(output_buffer)

            ptr = acl.rt.malloc_host(output_data_size)
            if not ptr:
                raise RuntimeError("分配主机内存失败")

            # 拷贝数据到主机
            kind = 1  # ACL_MEMCPY_DEVICE_TO_HOST
            ret = acl.rt.memcpy(
                ptr, output_data_size,
                output_data_buffer_addr, output_data_size,
                kind
            )
            if ret:
                raise RuntimeError(f"输出数据拷贝失败: {ret}")

            # 转换为numpy数组
            if "ptr_to_bytes" in dir(acl.util):
                bytes_data = acl.util.ptr_to_bytes(ptr, output_data_size)
                data = np.frombuffer(bytes_data, dtype=np.float32)
            else:
                data = acl.util.ptr_to_numpy(ptr, [1000], 11)  # NPY_FLOAT32

            output_data.append(data)
            acl.rt.free_host(ptr)

        total_time = time.time() - start_time

        # 处理分类结果
        if output_data:
            scores = output_data[0]
            predicted_class = np.argmax(scores)
            confidence = float(scores[predicted_class])

            class_name = self.labels.get(str(predicted_class), f"class_{predicted_class}")

            result = {
                'class_id': int(predicted_class),
                'class_name': class_name,
                'confidence': confidence,
                'all_scores': scores.tolist()[:5]  # 前5个分数
                'preprocess_time': preprocess_time * 1000,
                'inference_time': inference_time * 1000,
                'total_time': total_time * 1000
            }

            return result
        else:
            return None

    def print_result(self, result, image_name=""):
        """打印分类结果"""
        if result:
            print(f"\n🎯 ===== {image_name} 分类结果 =====")
            print(f"类别ID: {result['class_id']}")
            print(f"类别名: {result['class_name']}")
            print(f"置信度: {result['confidence']:.4f}")
            print(f"预处理时间: {result['preprocess_time']:.2f}ms")
            print(f"推理时间: {result['inference_time']:.2f}ms")
            print(f"总时间: {result['total_time']:.2f}ms")
            print(f"Top5分数: {[f'{s:.4f}' for s in result['all_scores']]}")
        else:
            print(f"\n❌ {image_name} 分类失败")

    def cleanup(self):
        """清理资源"""
        if self.dataset:
            acl.mdl.destroy_dataset(self.dataset)
        if self.model_id:
            acl.mdl.unload(self.model_id)
        if self.model_desc:
            acl.mdl.destroy_desc(self.model_desc)
        if self.context:
            acl.rt.destroy_context(self.context)
        acl.rt.reset_device(0)
        acl.finalize()
        print("✅ 资源清理完成")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='ResNet50图像分类推理脚本')
    parser.add_argument('--image', '-i', type=str,
                       default='../../data/test_images/dog1_1024_683.jpg',
                       help='输入图片路径')
    parser.add_argument('--topk', '-k', type=int, default=5,
                       help='显示Top-K预测结果')

    args = parser.parse_args()

    # 创建分类器
    classifier = ResNet50Classifier()

    try:
        if not os.path.exists(args.image):
            print(f"❌ 图片不存在: {args.image}")
            return

        # 执行分类
        result = classifier.classify(args.image)
        classifier.print_result(result, os.path.basename(args.image))

    except KeyboardInterrupt:
        print("\n⏹️ 用户中断")
    except Exception as e:
        print(f"❌ 运行错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        classifier.cleanup()

if __name__ == '__main__':
    main()