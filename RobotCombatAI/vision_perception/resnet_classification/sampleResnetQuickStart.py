import os
import cv2
import numpy as np
import acl
from label import label

NPY_FLOAT32 = 11
ACL_MEMCPY_HOST_TO_HOST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
ACL_MEMCPY_DEVICE_TO_DEVICE = 3
ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_DEVICE, ACL_HOST = 0, 1
ACL_SUCCESS = 0
min_chn = np.array([123.675, 116.28, 103.53], dtype=np.float32)
var_reci_chn = np.array([0.0171247538316637, 0.0175070028011204, 0.0174291938997821], dtype=np.float32)

class Sample_resnet_quick_start(object):
    def __init__(self, device_id, model_path, model_width, model_height):
        self.device_id = device_id      # int
        self.context = None             # pointer
        self.stream = None

        self.model_width = model_width
        self.model_height = model_height
        self.model_id = None            # pointer
        self.model_path = model_path    # string
        self.model_desc = None          # pointer when using
        self.input_dataset = None
        self.output_dataset = None
        self.input_buffer = None
        self.output_buffer = None
        self.input_buffer_size = None
        self.image_bytes = None
        self.image_name = None
        self.dir = None
        self.image = None
        self.runMode_ = acl.rt.get_run_mode()

    def init_resource(self):
        # init acl resource
        ret = acl.init()
        if ret != ACL_SUCCESS:
            print('acl init failed, errorCode is', ret)

        ret = acl.rt.set_device(self.device_id)
        if ret != ACL_SUCCESS:
            print('set device failed, errorCode is', ret)
            
        self.context, ret = acl.rt.create_context(self.device_id)
        if ret != ACL_SUCCESS:
            print('create context failed, errorCode is', ret)

        self.stream, ret = acl.rt.create_stream()
        if ret != ACL_SUCCESS:
            print('create stream failed, errorCode is', ret)

        # load model from file
        self.model_id, ret = acl.mdl.load_from_file(self.model_path)
        if ret != ACL_SUCCESS:
            print('load model failed, errorCode is', ret)

        # create description of model
        self.model_desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.model_desc, self.model_id)
        if ret != ACL_SUCCESS:
            print('get desc failed, errorCode is', ret)

        # create data set of input
        self.input_dataset = acl.mdl.create_dataset()
        input_index = 0
        self.input_buffer_size = acl.mdl.get_input_size_by_index(self.model_desc, input_index)
        self.input_buffer, ret = acl.rt.malloc(self.input_buffer_size, ACL_MEM_MALLOC_HUGE_FIRST)
        input_data = acl.create_data_buffer(self.input_buffer, self.input_buffer_size)
        self.input_dataset, ret = acl.mdl.add_dataset_buffer(self.input_dataset, input_data)
        if ret != ACL_SUCCESS:
            print('acl.mdl.add_dataset_buffer failed, errorCode is', ret)

        # create data set of output
        self.output_dataset = acl.mdl.create_dataset()
        output_index = 0
        output_buffer_size = acl.mdl.get_output_size_by_index(self.model_desc, output_index)
        self.output_buffer, ret = acl.rt.malloc(output_buffer_size, ACL_MEM_MALLOC_HUGE_FIRST)
        output_data = acl.create_data_buffer(self.output_buffer, output_buffer_size)
        self.output_dataset, ret = acl.mdl.add_dataset_buffer(self.output_dataset, output_data)
        if ret != ACL_SUCCESS:
            print('acl.mdl.add_dataset_buffer failed, errorCode is', ret)

    def process_input(self, input_path):
        # read image from file by opencv
        self.dir, self.image_name = os.path.split(input_path)
        input_path = os.path.abspath(input_path)
        image = cv2.imread(input_path)
        self.image = image
        # zoom image to model_width * model_height
        resize_image = cv2.resize(image, (self.model_width, self.model_height))

        # switch channel BGR to RGB
        image_rgb = cv2.cvtColor(resize_image, cv2.COLOR_BGR2RGB)
        image_rgb = image_rgb.astype(np.float32)

        # data standardization
        image_rgb = (image_rgb - min_chn) * var_reci_chn

        # HWC to CHW
        image_rgb = image_rgb.transpose([2, 0, 1]).copy()
        self.image_bytes = np.frombuffer(image_rgb.tobytes())

    def inference(self):
        # pass image data to input data buffer
        if self.runMode_ == ACL_DEVICE:
            kind = ACL_MEMCPY_DEVICE_TO_DEVICE
        else:
            kind = ACL_MEMCPY_HOST_TO_DEVICE
        if "bytes_to_ptr" in dir(acl.util):
            bytes_data = self.image_bytes.tobytes()
            ptr = acl.util.bytes_to_ptr(bytes_data)
        else:
            ptr = acl.util.numpy_to_ptr(self.image_bytes)
        ret = acl.rt.memcpy(self.input_buffer,
                            self.input_buffer_size,
                            ptr,
                            self.input_buffer_size,
                            kind)

        if ret != ACL_SUCCESS:
            print('memcpy failed, errorCode is', ret)

        # inference
        ret = acl.mdl.execute(self.model_id,
                              self.input_dataset,
                              self.output_dataset)
        if ret != ACL_SUCCESS:
            print('execute failed, errorCode is', ret)

    def get_result(self):
        # get result from output data set
        output_index = 0
        output_data_buffer = acl.mdl.get_dataset_buffer(self.output_dataset, output_index)
        output_data_buffer_addr = acl.get_data_buffer_addr(output_data_buffer)
        output_data_size = acl.get_data_buffer_size(output_data_buffer)
        ptr, ret = acl.rt.malloc_host(output_data_size)

        # copy device output data to host
        if self.runMode_ == ACL_DEVICE:
            kind = ACL_MEMCPY_DEVICE_TO_HOST
        else:
            kind = ACL_MEMCPY_HOST_TO_HOST
        ret = acl.rt.memcpy(ptr,
                            output_data_size,
                            output_data_buffer_addr,
                            output_data_size,
                            ACL_MEMCPY_HOST_TO_DEVICE)
        if ret != ACL_SUCCESS:
            print('memcpy failed, errorCode is', ret)

        index = 0
        dims, ret = acl.mdl.get_cur_output_dims(self.model_desc, index)

        if ret != ACL_SUCCESS:
            print('get output dims failed, errorCode is', ret)
        out_dim = dims['dims']

        if "ptr_to_bytes" in dir(acl.util):
            bytes_data = acl.util.ptr_to_bytes(ptr, output_data_size)
            data = np.frombuffer(bytes_data, dtype=np.float32).reshape(out_dim)
        else:
            data = acl.util.ptr_to_numpy(ptr, out_dim, NPY_FLOAT32)

        # do data processing with softmax
        data = data.flatten()
        vals = np.exp(data)/np.sum(np.exp(data))

        # get tge index of max confidence 
        top_index = vals.argsort()[-1]

        label_string = label.get(str(top_index))
        assert label_string, "the key of label is not exist"
        label_class = ",".join(label_string)
        text = f"label:{top_index}  conf:{vals[top_index]:06f}  class:{label_class}"

        # write image to ../out/
        cv2.putText(self.image, text, (0, 35),
 			            cv2.FONT_HERSHEY_TRIPLEX, 1.0, (255, 0, 0), 2)
        output_dir = os.path.join(os.path.dirname(self.dir),"out/")
        os.makedirs(output_dir, exist_ok=True)
        output_name = f"out_{self.image_name}"
        output_path = os.path.join(output_dir, output_name)
        cv2.imwrite(output_path,self.image)
        print(output_name)
        print(text)

        ret = acl.rt.free_host(ptr)
        if ret != ACL_SUCCESS:
            print('free host failed, errorCode is', ret)

    def release_resource(self):
        # release resource includes acl resource, data set and unload model
        acl.rt.free(self.input_buffer)
        acl.mdl.destroy_dataset(self.input_dataset)

        acl.rt.free(self.output_buffer)
        acl.mdl.destroy_dataset(self.output_dataset)
        ret = acl.mdl.unload(self.model_id)
        if ret != ACL_SUCCESS:
            print('unload model failed, errorCode is', ret)

        if self.model_desc:
            acl.mdl.destroy_desc(self.model_desc)
            self.model_desc = None

        if self.stream:
            ret = acl.rt.destroy_stream(self.stream)
            if ret != ACL_SUCCESS:
                print('destroy stream failed, errorCode is', ret)
            self.stream = None

        if self.context:
            ret = acl.rt.destroy_context(self.context)
            if ret != ACL_SUCCESS:
                print('destroy context failed, errorCode is', ret)
            self.context = None

        ret = acl.rt.reset_device(self.device_id)
        if ret != ACL_SUCCESS:
            print('reset device failed, errorCode is', ret)
            
        ret = acl.finalize()
        if ret != ACL_SUCCESS:
            print('finalize failed, errorCode is', ret)

if __name__ == '__main__':
    device = 0
    model_width = 224
    model_height = 224
    current_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(current_dir, "../model/resnet50.om")
    if not os.path.exists(model_path):
        raise Exception("the model is not exist")

    # read all file path of image
    images_path = os.path.join(os.path.dirname(current_dir), "data")
    if not os.path.exists(images_path):
        raise Exception("the directory is not exist")
    all_path = []
    for path in os.listdir(images_path):
        if path != '.keep':
            total_path = os.path.join(images_path, path)
            all_path.append(total_path)
    if len(all_path) == 0:
        raise Exception("the directory is empty, please download image")

    # inference
    net = Sample_resnet_quick_start(device, model_path, model_width, model_height)
    net.init_resource()
    for path in all_path:
        net.process_input(path)
        net.inference()
        net.get_result()
    print("*****run finish******")
    net.release_resource()