# file: modules/workers.py
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
# Import audio_player đã tạo
from .audio import audio_player

class SessionMonitorWorker(threading.Thread):
    """
    Luồng nền chuyên giám sát thời gian của phiên bắn.
    Nếu hết giờ, nó sẽ tự động gọi hàm để kết thúc phiên.
    """
    def __init__(self, session_lock, get_session_state_func, end_session_func, interval=1):
        super().__init__(daemon=True, name="SessionMonitor")
        self.session_lock = session_lock
        self.get_session_state_func = get_session_state_func
        self.end_session_func = end_session_func
        self.interval = interval # Tần suất kiểm tra (mỗi giây)
        logging.info("Luồng Giám sát Phiên bắn đã được khởi tạo.")

    def run(self):
        logging.info("Luồng Giám sát Phiên bắn bắt đầu hoạt động.")
        while True:
            # Lấy trạng thái phiên bắn một cách an toàn từ main.py
            is_active, end_time = self.get_session_state_func()
            
            # Chỉ kiểm tra nếu phiên đang hoạt động
            if is_active and time.time() > end_time:
                logging.info("Phát hiện phiên bắn đã hết thời gian quy định.")
                self.end_session_func("Hết thời gian") # Gọi hàm kết thúc phiên
            
            time.sleep(self.interval)
            
class StatusReporterWorker(threading.Thread):
    """
    Một luồng nền chuyên giám sát trạng thái của các thành phần khác (camera, cò)
    và gửi báo cáo định kỳ về server qua SocketIO.
    """
    def __init__(self, send_status_func, trigger_listener, camera, interval=2):
        super().__init__(daemon=True, name="StatusReporter")
        self.send_status_func = send_status_func
        self.trigger_listener = trigger_listener
        self.camera = camera
        self.interval = interval

    def run(self):
        logging.info("Luồng Giám sát Trạng thái bắt đầu.")
        while True:
            # Kiểm tra trạng thái Cò bắn
            if self.trigger_listener.is_connected():
                self.send_status_func('trigger', 'ready')
            else:
                self.send_status_func('trigger', 'disconnected')

            # Kiểm tra trạng thái Camera
            if self.camera.is_running():
                self.send_status_func('video', 'ready')
            else:
                self.send_status_func('video', 'disconnected')
            
            time.sleep(self.interval)
            
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

    def is_connected(self):
        """Hàm để worker khác kiểm tra trạng thái kết nối."""
        return self.device is not None

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

    def run(self):
        logging.info("Đang tìm kiếm cò bắn Bluetooth...")
        while True:
            try:
                if self.device is None:
                    # Vòng lặp tìm kiếm, nhưng không gửi status ở đây nữa
                    self.device = self.find_device()
                    if self.device is None:
                        time.sleep(5)
                        continue
                    logging.info(f"✅ Đã kết nối với cò bắn: {self.device.name}")
                    self.device.grab()
                for event in self.device.read_loop():
                    if event.type == evdev.ecodes.EV_KEY and event.code == self.key_code:
                        if event.value == 1 and not self.trigger_held:
                            self.trigger_held = True
                            self.burst_session_id += 1
                            threading.Thread(target=self.fire_one_burst, args=(self.burst_session_id,)).start()

                        elif event.value == 0:
                            self.trigger_held = False
            except (IOError, OSError) as e:
                logging.warning(f"⚠️ Mất kết nối cò bắn: {e}. Đang tìm kiếm lại...")
                if self.device:
                    try:
                        self.device.ungrab()
                    except: pass # Bỏ qua lỗi nếu ungrab không thành công
                self.device = None # Quan trọng: đặt lại device là None
                time.sleep(2) 
                              
class ProcessingWorker(threading.Thread):
    # Sửa đổi: Thêm sio_client vào hàm khởi tạo
    def __init__(self, queue, sio_client):
        super().__init__(daemon=True, name="ProcessingWorker")
        self.queue = queue
        self.sio_client = sio_client # <-- Lưu lại sio_client
        self.base_captures_dir = "captures"
        os.makedirs(self.base_captures_dir, exist_ok=True)
        logging.info("Luồng Xử lý Ảnh đã được khởi tạo.")

    def run(self):
        logging.info("Luồng Xử lý Ảnh bắt đầu hoạt động.")
        while True:
            try:
                shot_data = self.queue.get()
                burst_id = shot_data["burst_id"]
                shot_id = shot_data["shot_id"]
                logging.info(f"--- (Loạt {burst_id}, Phát {shot_id})! --- Đang xử lý...")

                burst_dir = os.path.join(self.base_captures_dir, f"burst_{burst_id}")
                os.makedirs(burst_dir, exist_ok=True)

                frame_to_process = shot_data["frame"]
                zoom_at_shot = shot_data["zoom"]
                center_at_shot = shot_data["center"]

                rotated_frame = cv2.rotate(frame_to_process, cv2.ROTATE_90_CLOCKWISE)
                final_image = draw_crosshair_on_frame(rotated_frame, zoom_at_shot, center_at_shot)

                # --- LOGIC CŨ: LƯU ẢNH (Vẫn giữ lại để backup) ---
                filename = os.path.join(burst_dir, f"shot_{shot_id}.jpg")
                cv2.imwrite(filename, final_image)
                logging.info(f"✅ Đã xử lý và lưu thành công file {filename}")

                # --- LOGIC MỚI: GỬI ẢNH VỀ SERVER ---
                if self.sio_client and self.sio_client.connected:
                    # Mã hóa ảnh sang định dạng JPEG rồi sang Base64
                    _, buffer = cv2.imencode('.jpg', final_image)
                    jpg_as_text = base64.b64encode(buffer).decode('utf-8')
                    
                    # Gửi dữ liệu base64 qua socket
                    logging.info(f"Gửi ảnh của phát bắn {shot_id} về server...")
                    self.sio_client.emit('new_shot_image', {
                        'shot_id': shot_id,
                        'image_data': f"data:image/jpeg;base64,{jpg_as_text}"
                    })
                # ----------------------------------------

                self.queue.task_done()
            except Exception as e:
                logging.error(f"Lỗi khi xử lý ảnh: {e}")

# ... (StreamerWorker và CommandPoller giữ nguyên không đổi) ...
class StreamerWorker(threading.Thread):
    def __init__(self, camera, upload_url, state_lock, get_state_func, fps):
        super().__init__(daemon=True, name="StreamerWorker")
        self.camera = camera
        self.upload_url = upload_url
        self.state_lock = state_lock
        self.get_state_func = get_state_func
        self.fps = fps

    def run(self):
        logging.info("Luồng gửi video bắt đầu hoạt động.")
        while True:
            # **SỬA LỖI QUAN TRỌNG**: Thêm kiểm tra camera có đang chạy không
            if not self.camera.is_running():
                time.sleep(1) # Nếu camera không chạy, đợi 1 giây rồi kiểm tra lại
                continue

            original_frame = self.camera.read()
            # Kiểm tra lại một lần nữa phòng trường hợp camera vừa ngắt kết nối
            if original_frame is None:
                continue

            rotated_frame = cv2.rotate(original_frame, cv2.ROTATE_90_CLOCKWISE)
            with self.state_lock:
                zoom_level, center_point = self.get_state_func()
            
            frame_to_send = draw_crosshair_on_frame(rotated_frame, zoom_level, center_point)
            (flag, encodedImage) = cv2.imencode(".jpg", frame_to_send, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not flag:
                continue

            try:
                requests.post(self.upload_url, data=bytearray(encodedImage), headers={'Content-Type': 'image/jpeg'}, timeout=0.5)
            except requests.exceptions.RequestException:
                # Lỗi kết nối khi gửi ảnh là bình thường, có thể bỏ qua log
                pass
            
            time.sleep(1 / self.fps)
            
class CommandPoller(threading.Thread):
    # SỬA ĐỔI: Thêm start_session_func vào hàm khởi tạo
    def __init__(self, poll_url, set_state_func, start_session_func, reset_session_func):
        super().__init__(daemon=True)
        self.poll_url = poll_url
        self.set_state_func = set_state_func
        self.start_session_func = start_session_func # <-- Lưu lại hàm được truyền vào
        self.reset_session_func = reset_session_func
    def run(self):
        logging.info("Bắt đầu lắng nghe lệnh từ server...")
        while True:
            try:
                response = requests.get(self.poll_url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    command = data.get('command')
                    if command:
                        command_type = command.get('type')
                        # Phân loại và xử lý lệnh
                        if command_type == 'start':
                            logging.info("Nhận được lệnh 'start'.")
                            self.start_session_func()
                        
                        # **THÊM MỚI: Xử lý lệnh 'reset'**
                        elif command_type == 'reset':
                            logging.info("Nhận được lệnh 'reset'.")
                            self.reset_session_func() # Gọi hàm reset từ main.py
                        
                        else:
                            # Xử lý các lệnh khác như zoom, center
                            self.set_state_func(command)

            except requests.exceptions.RequestException:
                # Lỗi kết nối là bình thường khi server chưa bật, nên ta có thể bỏ qua log này
                pass
            time.sleep(1) # Chờ 1 giây giữa mỗi lần hỏi