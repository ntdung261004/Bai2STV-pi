# file: modules/workers.py
import threading
import time
import queue
import evdev
import requests
import cv2
import logging
import os
from datetime import datetime
from .utils import draw_crosshair_on_frame
# Import audio_player đã tạo
from .audio import audio_player

class TriggerListener(threading.Thread):
    def __init__(self, device_name, key_code, camera, queue, state_lock, get_state_func, can_fire_func, decrement_bullet_func):
        super().__init__(daemon=True)
        self.device_name = device_name
        self.key_code = key_code
        self.camera = camera
        self.queue = queue
        self.device = None
        self.trigger_held = False
        self.state_lock = state_lock
        self.get_state_func = get_state_func
        self.can_fire_func = can_fire_func
        self.decrement_bullet_func = decrement_bullet_func
        self.burst_session_id = 0

    def find_device(self):
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for device in devices:
            if self.device_name.lower() in device.name.lower():
                return device
        return None
    
    def fire_one_burst(self, current_burst_id):
        shot_in_burst_index = 0
        while self.trigger_held:
            if self.can_fire_func():
                self.decrement_bullet_func()

                with self.state_lock:
                    zoom, center = self.get_state_func()
                
                frame = self.camera.read()
                if frame is not None:
                    # =================================================================
                    # SỬA LỖI: Bổ sung lại các key bị thiếu
                    # =================================================================
                    shot_id = f"{current_burst_id}-{shot_in_burst_index}"
                    shot_data = {
                        'frame': frame,
                        'timestamp': datetime.now(),
                        'shot_id': shot_id,           # ID duy nhất cho file ảnh
                        'burst_id': current_burst_id, # ID cho thư mục loạt bắn
                        'shot_index': shot_in_burst_index, # Index của phát bắn
                        'zoom': zoom,
                        'center': center
                    }
                    # =================================================================
                    self.queue.put(shot_data)
                    audio_player.play('shot')
                
                shot_in_burst_index += 1
                time.sleep(0.1) 
            else:
                logging.warning("Dừng loạt bắn do không đủ điều kiện (hết đạn/hết giờ).")
                break

    # THAY THẾ TOÀN BỘ HÀM run BẰNG HÀM DƯỚI ĐÂY
    def run(self):
        logging.info("Đang tìm kiếm cò bắn Bluetooth...")
        self.device = None
        while self.device is None:
            self.device = self.find_device()
            if not self.device:
                logging.warning(f"Chưa tìm thấy cò bắn '{self.device_name}'. Đang thử lại sau 5 giây...")
                time.sleep(5)
        
        logging.info(f"✅ Đã kết nối với cò bắn: {self.device.name} tại {self.device.path}")

        try:
            # =================================================================
            # THÊM MỚI: "Độc chiếm" thiết bị để ngăn hệ điều hành xử lý
            # =================================================================
            self.device.grab()
            logging.info("Đã độc chiếm thiết bị. Hệ điều hành sẽ không nhận tín hiệu âm lượng nữa.")
            # =================================================================

            for event in self.device.read_loop():
                if event.type == evdev.ecodes.EV_KEY and event.code == self.key_code:
                    if event.value == 1 and not self.trigger_held:
                        self.trigger_held = True
                        self.burst_session_id += 1
                        threading.Thread(target=self.fire_one_burst, args=(self.burst_session_id,)).start()

                    elif event.value == 0:
                        self.trigger_held = False
        finally:
            # =================================================================
            # THÊM MỚI: "Nhả" thiết bị ra khi chương trình kết thúc
            # =================================================================
            if self.device:
                self.device.ungrab()
                logging.info("Đã nhả thiết bị.")
            # =================================================================
                                                  
class ProcessingWorker(threading.Thread):
    def __init__(self, queue):
        super().__init__(daemon=True)
        self.queue = queue
        self.base_captures_dir = "captures"
        os.makedirs(self.base_captures_dir, exist_ok=True)

    def run(self):
        print("🛠️  Luồng xử lý ảnh đã sẵn sàng...")
        while True:
            try:
                shot_data = self.queue.get()
                
                burst_id = shot_data["burst_id"]
                shot_id = shot_data["shot_id"]
                
                print(f"--- (Loạt {burst_id}, Phát {shot_id})! --- Đang xử lý...")
                
                # Tạo thư mục con cho loạt bắn nếu chưa có
                burst_dir = os.path.join(self.base_captures_dir, f"burst_{burst_id}")
                os.makedirs(burst_dir, exist_ok=True)
                
                frame_to_process = shot_data["frame"]
                zoom_at_shot = shot_data["zoom"]
                center_at_shot = shot_data["center"]
                
                rotated_frame = cv2.rotate(frame_to_process, cv2.ROTATE_90_CLOCKWISE)
                final_image = draw_crosshair_on_frame(rotated_frame, zoom_at_shot, center_at_shot)
                
                # Lưu ảnh vào thư mục con tương ứng
                filename = os.path.join(burst_dir, f"shot_{shot_id}.jpg")
                cv2.imwrite(filename, final_image)
                print(f"✅ Đã xử lý và lưu thành công file {filename}")

                self.queue.task_done()
            except Exception as e:
                print(f"Lỗi khi xử lý ảnh: {e}")

# ... (StreamerWorker và CommandPoller giữ nguyên không đổi) ...
class StreamerWorker(threading.Thread):
    def __init__(self, camera, upload_url, state_lock, get_state_func, fps):
        super().__init__(daemon=True)
        self.camera = camera
        self.upload_url = upload_url
        self.state_lock = state_lock
        self.get_state_func = get_state_func
        self.fps = fps
    def run(self):
        print("Bắt đầu gửi luồng video tới server...")
        while True:
            original_frame = self.camera.read()
            if original_frame is None: continue
            rotated_frame = cv2.rotate(original_frame, cv2.ROTATE_90_CLOCKWISE)
            with self.state_lock:
                zoom_level, center_point = self.get_state_func()
            frame_to_send = draw_crosshair_on_frame(rotated_frame, zoom_level, center_point)
            (flag, encodedImage) = cv2.imencode(".jpg", frame_to_send, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not flag: continue
            try:
                requests.post(self.upload_url, data=bytearray(encodedImage), headers={'Content-Type': 'image/jpeg'}, timeout=1)
            except requests.exceptions.RequestException:
                pass
            time.sleep(1 / self.fps)
class CommandPoller(threading.Thread):
    # SỬA ĐỔI: Thêm start_session_func vào hàm khởi tạo
    def __init__(self, poll_url, set_state_func, start_session_func):
        super().__init__(daemon=True)
        self.poll_url = poll_url
        self.set_state_func = set_state_func
        self.start_session_func = start_session_func # <-- Lưu lại hàm được truyền vào

    def run(self):
        logging.info("Bắt đầu lắng nghe lệnh từ server...")
        while True:
            try:
                response = requests.get(self.poll_url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    command = data.get('command')
                    
                    if command:
                        # =================================================================
                        # SỬA ĐỔI: Xử lý lệnh 'start'
                        # =================================================================
                        if command.get('type') == 'start':
                            logging.info("Nhận được lệnh 'start' từ server.")
                            self.start_session_func() # Gọi hàm start_session từ main.py
                        else:
                            # Xử lý các lệnh khác như zoom, center
                            self.set_state_func(command)
                        # =================================================================

            except requests.exceptions.RequestException:
                # Lỗi kết nối là bình thường khi server chưa bật, nên ta có thể bỏ qua log này
                pass
            time.sleep(1) # Chờ 1 giây giữa mỗi lần hỏi