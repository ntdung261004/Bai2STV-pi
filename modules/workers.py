# file: modules/workers.py
import threading
import time
import queue
import evdev
import requests
import cv2
import os
from datetime import datetime
from .utils import draw_crosshair_on_frame
# Import audio_player đã tạo
from .audio import audio_player

class TriggerListener(threading.Thread):
    def __init__(self, device_name, key_code, camera, queue, state_lock, get_state_func):
        super().__init__(daemon=True)
        self.device_name = device_name
        self.key_code = key_code
        self.camera = camera
        self.queue = queue
        self.device = None
        self.trigger_held = False
        self.state_lock = state_lock
        self.get_state_func = get_state_func
        # Biến đếm số loạt bắn
        self.burst_session_id = 0

    def find_device(self):
        # ... (Hàm này giữ nguyên)
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for device in devices:
            if self.device_name.lower() in device.name.lower():
                print(f"✅ Đã tìm thấy cò bắn: {device.name} tại {device.path}")
                return device
        return None

    def fire_one_burst(self, current_burst_id):
        """Thực hiện một loạt bắn cho đến khi người dùng nhả cò."""
        shot_in_burst_index = 0
        while self.trigger_held:
            shot_in_burst_index += 1
            
            # PHÁT ÂM THANH
            audio_player.play('shot')

            frame = self.camera.read()
            with self.state_lock:
                zoom, center = self.get_state_func()
            
            if frame is not None and not self.queue.full():
                shot_data = {
                    "frame": frame.copy(),
                    "zoom": zoom,
                    "center": center.copy(),
                    "burst_id": current_burst_id,
                    "shot_id": shot_in_burst_index
                }
                self.queue.put(shot_data)
            
            time.sleep(0.1) # RATE_OF_FIRE_DELAY
        print(f"✅ Loạt bắn #{current_burst_id} hoàn tất với {shot_in_burst_index} phát.")

    def run(self):
        print("🔫 Luồng lắng nghe cò bắn đã sẵn sàng...")
        while True:
            try:
                if self.device is None:
                    self.device = self.find_device()
                    if self.device is None: time.sleep(5); continue
                    self.device.grab()
                for event in self.device.read_loop():
                    if event.type == evdev.ecodes.EV_KEY and event.code == self.key_code:
                        if event.value in [1, 2]: # Nhấn hoặc giữ
                            if not self.trigger_held:
                                self.trigger_held = True
                                # BẮT ĐẦU MỘT LOẠT BẮN MỚI
                                self.burst_session_id += 1
                                print(f"🔥 Bắt đầu loạt bắn #{self.burst_session_id}...")
                                threading.Thread(target=self.fire_one_burst, args=(self.burst_session_id,)).start()
                        elif event.value == 0: # Nhả cò
                            if self.trigger_held:
                                print("🛑 Ngừng bắn.")
                                self.trigger_held = False
            except (IOError, OSError) as e:
                print(f"⚠️ Thiết bị cò bắn đã bị ngắt kết nối: {e}. Đang tìm kiếm lại...")
                self.trigger_held = False
                if self.device: self.device.close()
                self.device = None
                time.sleep(2)

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
    def __init__(self, poll_url, set_state_func):
        super().__init__(daemon=True)
        self.poll_url = poll_url
        self.set_state_func = set_state_func
    def run(self):
        print("Bắt đầu lắng nghe lệnh từ server...")
        while True:
            try:
                response = requests.get(self.poll_url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    command = data.get('command')
                    if command:
                        self.set_state_func(command)
            except requests.exceptions.RequestException:
                pass
            time.sleep(0.5)