# file: modules/workers.py (phiên bản OOP, dựa trên logic gốc của bạn)
import threading
import time
import queue
import evdev
import requests
import cv2
import logging
import os
import base64
from datetime import datetime
from .utils import draw_crosshair_on_frame
from .audio import audio_player
from .yolo_predictor import analyze_shot

# LƯU Ý: Các lớp Worker đã được cập nhật để nhận vào một đối tượng 'app' duy nhất.

class SessionMonitorWorker(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name="SessionMonitor")
        self.app = app
        logging.info("Luồng Giám sát Phiên bắn đã được khởi tạo.")

    def run(self):
        logging.info("Luồng Giám sát Phiên bắn bắt đầu hoạt động.")
        while not self.app.is_stopping():
            is_active, end_time, _ = self.app.get_session_state()
            if is_active and time.time() > end_time:
                logging.info("Phát hiện phiên bắn đã hết thời gian quy định.")
                self.app.end_session("Hết thời gian")
            time.sleep(1)

class StatusReporterWorker(threading.Thread):
    def __init__(self, app, trigger_listener, camera):
        super().__init__(daemon=True, name="StatusReporter")
        self.app = app
        self.trigger_listener = trigger_listener
        self.camera = camera

    def run(self):
        logging.info("Luồng Giám sát Trạng thái bắt đầu.")
        while not self.app.is_stopping():
            if self.trigger_listener.is_connected():
                self.app.send_status_update('trigger', 'ready')
            else:
                self.app.send_status_update('trigger', 'disconnected')

            if self.camera.is_running():
                self.app.send_status_update('video', 'ready')
            else:
                self.app.send_status_update('video', 'disconnected')
            time.sleep(2)

class TriggerListener(threading.Thread):
    def __init__(self, app, device_name, key_code):
        super().__init__(daemon=True, name="TriggerListener")
        self.app = app
        self.device_name = device_name
        self.key_code = key_code
        self.device = None
        self.trigger_held = False
        self.burst_session_id = 0
        self._is_connected = False

    def is_connected(self):
        return self._is_connected

    def find_device(self):
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for device in devices:
            if self.device_name.lower() in device.name.lower():
                return device
        return None

    def fire_one_burst(self, current_burst_id):
        shot_in_burst_index = 0
        while self.trigger_held:
            if self.app.is_stopping(): break

            if self.app.can_fire():
                self.app.decrement_bullet()
                frame = self.app.camera.read()
                if frame is not None:
                    zoom, center = self.app.get_current_state()
                    shot_id = f"{current_burst_id}-{shot_in_burst_index}"
                    shot_data = {
                        'frame': frame, 'timestamp': datetime.now(), 'shot_id': shot_id,
                        'burst_id': current_burst_id, 'shot_index': shot_in_burst_index,
                        'zoom': zoom, 'center': center
                    }
                    self.app.processing_queue.put(shot_data)
                    audio_player.play('shot')
                else:
                    logging.error("LỖI: Không thể đọc khung hình từ camera khi bắn.")
                
                shot_in_burst_index += 1
                time.sleep(0.1)
            else:
                logging.warning("Dừng loạt bắn do không đủ điều kiện (hết đạn/hết giờ/phiên dừng).")
                break

    def run(self):
        logging.info(f"Bắt đầu tìm kiếm cò bắn '{self.device_name}'...")
        while not self.app.is_stopping():
            try:
                if self.device is None:
                    self.device = self.find_device()
                    if self.device is None:
                        self._is_connected = False
                        time.sleep(5)
                        continue
                    logging.info(f"✅ Đã kết nối với cò bắn: {self.device.name}")
                    self.device.grab()
                    self._is_connected = True
                
                for event in self.device.read_loop():
                    if self.app.is_stopping(): break
                    if event.type == evdev.ecodes.EV_KEY and event.code == self.key_code:
                        if event.value == 1 and not self.trigger_held: # Key press
                            self.trigger_held = True
                            self.burst_session_id += 1
                            threading.Thread(target=self.fire_one_burst, args=(self.burst_session_id,)).start()
                        elif event.value == 0: # Key release
                            self.trigger_held = False
            except (IOError, OSError) as e:
                logging.warning(f"⚠️ Mất kết nối cò bắn: {e}. Đang tìm kiếm lại...")
                if self.device:
                    try: self.device.ungrab()
                    except: pass
                self.device = None
                self._is_connected = False
                time.sleep(2)

class ProcessingWorker(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name="ProcessingWorker")
        self.app = app
        self.base_captures_dir = "captures"
        self.yolo_dataset_dir = "yolo_dataset"
        os.makedirs(self.base_captures_dir, exist_ok=True)
        os.makedirs(self.yolo_dataset_dir, exist_ok=True)
        logging.info("Luồng Xử lý Ảnh đã được khởi tạo.")

    def run(self):
        logging.info("Luồng Xử lý Ảnh bắt đầu hoạt động.")
        while not self.app.is_stopping():
            try:
                shot_data = self.app.processing_queue.get(timeout=1)
                
                rotated_frame = cv2.rotate(shot_data["frame"], cv2.ROTATE_90_CLOCKWISE)
                
                hit_target_name = analyze_shot(rotated_frame, shot_data["center"])
                if hit_target_name:
                    self.app.register_hit(hit_target_name)
                
                # Lưu ảnh và gửi review (logic gốc)
                time_str = shot_data["timestamp"].strftime("%Y%m%d_%H%M%S_%f")
                yolo_image_path = os.path.join(self.yolo_dataset_dir, f"{time_str}.jpg")
                cv2.imwrite(yolo_image_path, rotated_frame)
                
                final_image_for_review = draw_crosshair_on_frame(rotated_frame, shot_data["zoom"], shot_data["center"])
                _, buffer = cv2.imencode('.jpg', final_image_for_review)
                jpg_as_text = base64.b64encode(buffer).decode('utf-8')
                self.app.sio.emit('new_shot_image', { 'shot_id': shot_data['shot_id'], 'image_data': f"data:image/jpeg;base64,{jpg_as_text}" })
                
                # Kiểm tra hết đạn sau khi xử lý
                is_active, _, ammo_left = self.app.get_session_state()
                if is_active and ammo_left == 0:
                    logging.info("Xử lý xong ảnh cuối và phát hiện hết đạn. Kết thúc phiên.")
                    self.app.end_session('Hết đạn')

                self.app.processing_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"Lỗi trong ProcessingWorker: {e}", exc_info=True)

class StreamerWorker(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name="StreamerWorker")
        self.app = app

    def run(self):
        logging.info("Luồng gửi video bắt đầu hoạt động.")
        while not self.app.is_stopping():
            if not self.app.camera.is_running():
                time.sleep(1)
                continue

            original_frame = self.app.camera.read()
            if original_frame is None: continue

            rotated_frame = cv2.rotate(original_frame, cv2.ROTATE_90_CLOCKWISE)
            zoom_level, center_point = self.app.get_current_state()
            frame_to_send = draw_crosshair_on_frame(rotated_frame, zoom_level, center_point)
            
            flag, encodedImage = cv2.imencode(".jpg", frame_to_send, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not flag: continue

            try:
                requests.post(self.app.video_upload_url, data=bytearray(encodedImage), headers={'Content-Type': 'image/jpeg'}, timeout=0.5)
            except requests.exceptions.RequestException:
                pass
            
            time.sleep(1 / self.app.fps)

class CommandPoller(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name="CommandPoller")
        self.app = app

    def run(self):
        logging.info("Bắt đầu lắng nghe lệnh từ server...")
        while not self.app.is_stopping():
            try:
                response = requests.get(self.app.command_poll_url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    command = data.get('command')
                    if command:
                        command_type = command.get('type')
                        if command_type == 'start':
                            self.app.start_session()
                        elif command_type == 'reset':
                            self.app.reset_session()
                        else:
                            self.app.set_state_from_command(command)
            except requests.exceptions.RequestException:
                pass
            time.sleep(1)