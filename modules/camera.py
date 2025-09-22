# file: modules/camera.py
import cv2
import threading
import time
import logging

class Camera:
    def __init__(self, src=0, width=640, height=480):
        self.src = src
        self.width = width
        self.height = height
        self.stream = None
        self.grabbed = False
        self.frame = None
        self.stopped = False
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self.update, args=(), daemon=True).start()
        return self

    def update(self):
        while not self.stopped:
            if self.stream is None or not self.stream.isOpened():
                logging.info("Đang thử kết nối tới camera...")
                self.stream = cv2.VideoCapture(self.src)
                if self.stream.isOpened():
                    self.stream.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    self.stream.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    logging.info("✅ Kết nối camera thành công!")
                else:
                    self.stream.release()
                    self.stream = None
                    with self.lock:
                        self.grabbed = False
                        # **SỬA LỖI QUAN TRỌNG**: Khi kết nối thất bại, đặt frame là None
                        self.frame = None
                    time.sleep(3.0)
                    continue

            is_read, frame = self.stream.read()

            with self.lock:
                self.grabbed = is_read
                if is_read:
                    self.frame = frame
                else:
                    # **SỬA LỖI QUAN TRỌNG**: Khi đọc thất bại, đặt frame là None
                    self.frame = None
                    logging.warning("⚠️ Không thể đọc khung hình, camera có thể đã mất kết nối.")
                    self.stream.release()
                    self.stream = None
    
    def read(self):
        with self.lock:
            # Sửa đổi nhỏ: Trả về một bản sao để tránh xung đột luồng
            if self.frame is not None:
                return self.frame.copy()
            return None

    def is_running(self):
        with self.lock:
            # Trạng thái chạy nghĩa là không bị dừng và đang đọc được frame
            return not self.stopped and self.grabbed

    def stop(self):
        self.stopped = True
        if self.stream:
            self.stream.release()