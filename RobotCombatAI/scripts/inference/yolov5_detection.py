#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YOLOv5目标检测推理脚本
用于机器人格斗竞赛中的敌人和目标检测

作者: RobotCombatAI团队
日期: 2025-12-08
"""

import os
import sys
import time
import argparse
import numpy as np
import cv2
import torch

# 设置环境变量
os.environ['ASCEND_TOOLKIT_HOME'] = '/usr/local/Ascend/ascend-toolkit/8.0.0'
os.environ['ASCEND_OPP_PATH'] = '/usr/local/Ascend/ascend-toolkit/8.0.0/opp'

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), '../../vision_perception/yolo_detection'))
sys.path.append(os.path.join(os.path.dirname(__file__), '../../vision_perception/acl_utils'))

try:
    from objDet_yolov5 import YoloV5, init_acl, deinit_acl, DEVICE_ID
    from det_utils import letterbox, scale_coords, nms, draw_bbox
except ImportError as e:
    print(f"❌ 导入错误: {e}")
    print("请确保在正确的环境中运行此脚本")
    sys.exit(1)

# 配置参数
YOLOV5_CONFIG = {
    'model_path': '../../models/om_models/yolov5s_cylinder_cls3_20230822_640640_f32.om',
    'input_size': (640, 640),
    'conf_threshold': 0.25,
    'iou_threshold': 0.45,
    'classes': ['cylinder_0', 'cylinder_1', 'cylinder_2'],  # 3分类
    'colors': [(255, 0, 0), (0, 255, 0), (0, 0, 255)]  # 红绿蓝
}

class YOLOv5Detector:
    """YOLOv5目标检测器"""

    def __init__(self, config=None):
        self.config = YOLOV5_CONFIG if config is None else config
        self.model = None
        self.context = None
        self.init_model()

    def init_model(self):
        """初始化模型和ACL环境"""
        print("🎯 初始化YOLOv5检测器...")

        # 初始化ACL
        self.context = init_acl(DEVICE_ID)

        # 加载模型
        print(f"📁 加载模型: {self.config['model_path']}")
        self.model = YoloV5(self.config['model_path'])
        print("✅ YOLOv5模型初始化完成")

    def detect(self, image_path, save_result=True):
        """
        执行目标检测

        Args:
            image_path (str): 输入图片路径
            save_result (bool): 是否保存检测结果

        Returns:
            list: 检测结果列表 [[x1, y1, x2, y2, conf, class_id], ...]
        """
        print(f"🖼️  处理图片: {image_path}")

        # 读取图片
        img0 = cv2.imread(image_path)
        if img0 is None:
            print(f"❌ 无法读取图片: {image_path}")
            return []

        print(f"📊 原图尺寸: {img0.shape}")

        # 执行推理
        start_time = time.time()
        pred_all = self.model.infer(img0)
        inference_time = time.time() - start_time

        print(f"⚡ 推理耗时: {inference_time*1000:.2f}ms")
        print(f"📊 检测结果形状: {pred_all.shape}")

        # 处理检测结果
        results = []
        if len(pred_all) > 0:
            # 绘制检测结果
            img_result = img0.copy()

            for i, det in enumerate(pred_all):
                x1, y1, x2, y2, conf, cls = det
                class_id = int(cls)
                class_name = self.config['classes'][class_id] if class_id < len(self.config['classes']) else f'class_{class_id}'
                confidence = float(conf)
                color = self.config['colors'][class_id % len(self.config['colors'])]

                # 添加到结果列表
                results.append({
                    'class_id': class_id,
                    'class_name': class_name,
                    'confidence': confidence,
                    'bbox': [float(x1), float(y1), float(x2), float(y2)],
                    'center': [(float(x1)+float(x2))/2, (float(y1)+float(y2))/2],
                    'area': (float(x2)-float(x1)) * (float(y2)-float(y1))
                })

                # 绘制边界框
                x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
                cv2.rectangle(img_result, (x1, y1), (x2, y2), color, 2)

                # 添加标签
                label = f'{class_name} {confidence:.2f}'
                cv2.putText(img_result, label, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                # 添加序号
                cv2.putText(img_result, f'#{i+1}', (x1, y1+20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            # 保存结果
            if save_result:
                result_path = f'yolov5_result_{os.path.basename(image_path)}'
                cv2.imwrite(result_path, img_result)
                print(f"💾 结果已保存到: {result_path}")

        return results

    def print_results(self, results, image_name=""):
        """打印检测结果"""
        print(f"\n🎯 ===== {image_name} 检测结果 =====")
        print("格式: [序号] [类别] [置信度] [中心坐标] [边界框]")
        print("=" * 70)

        if results:
            for i, result in enumerate(results):
                center_x, center_y = result['center']
                bbox = result['bbox']
                print(f"📍 [{i+1:2d}] {result['class_name']:12} {result['confidence']:.4f} "
                      f"({center_x:6.1f}, {center_y:6.1f}) "
                      f"({bbox[0]:4.0f},{bbox[1]:4.0f}) ({bbox[2]:4.0f},{bbox[3]:4.0f})")
            print(f"\n🎯 检测到 {len(results)} 个目标")
        else:
            print("❌ 未检测到任何目标")

    def cleanup(self):
        """清理资源"""
        if self.model:
            self.model.release_resource()
        if self.context:
            deinit_acl(self.context, DEVICE_ID)
        print("✅ 资源清理完成")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='YOLOv5目标检测推理脚本')
    parser.add_argument('--image', '-i', type=str,
                       default='../../data/test_images/1692673758.632568_1.jpg',
                       help='输入图片路径')
    parser.add_argument('--save', '-s', action='store_true',
                       help='保存检测结果图片')
    parser.add_argument('--batch', '-b', action='store_true',
                       help='批量检测模式')

    args = parser.parse_args()

    # 创建检测器
    detector = YOLOv5Detector()

    try:
        if args.batch:
            # 批量检测模式
            print("🔄 批量检测模式")
            test_images = [
                '../../data/test_images/1692673758.632568_1.jpg',
                '../../data/test_images/world_cup.jpg',
                '../../data/test_images/dog1_1024_683.jpg',
                '../../data/test_images/dog2_1024_683.jpg'
            ]

            all_results = []
            for img_path in test_images:
                if os.path.exists(img_path):
                    results = detector.detect(img_path, args.save)
                    all_results.extend(results)
                    detector.print_results(results, os.path.basename(img_path))
                    print("-" * 50)
                else:
                    print(f"⚠️  图片不存在: {img_path}")

            print(f"\n📊 总计检测到 {len(all_results)} 个目标")

        else:
            # 单图片检测模式
            if not os.path.exists(args.image):
                print(f"❌ 图片不存在: {args.image}")
                return

            results = detector.detect(args.image, args.save)
            detector.print_results(results, os.path.basename(args.image))

    except KeyboardInterrupt:
        print("\n⏹️ 用户中断")
    except Exception as e:
        print(f"❌ 运行错误: {e}")
    finally:
        detector.cleanup()

if __name__ == '__main__':
    main()