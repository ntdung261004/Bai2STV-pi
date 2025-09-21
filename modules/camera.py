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
        self.stream = None # Sẽ được khởi tạo trong luồng
        self.grabbed = False
        self.frame = None
        self.stopped = False
        self.lock = threading.Lock()

    def start(self):
        # Khởi động luồng nền để đọc khung hình từ camera
        threading.Thread(target=self.update, args=(), daemon=True).start()
        return self

    # =================================================================
    # THAY THẾ TOÀN BỘ HÀM UPDATE BẰNG HÀM NÀY
    # =================================================================
    def update(self):
        """
        Luồng chạy nền liên tục đọc khung hình và tự động kết nối lại nếu mất.
        """
        while not self.stopped:
            if self.stream is None or not self.stream.isOpened():
                # Nếu chưa có stream hoặc stream đã mất, thử kết nối
                logging.info("Đang thử kết nối tới camera...")
                self.stream = cv2.VideoCapture(self.src)
                if self.stream.isOpened():
                    self.stream.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    self.stream.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    logging.info("✅ Kết nối camera thành công!")
                else:
                    # Nếu kết nối thất bại, giải phóng và chờ để thử lại
                    self.stream.release()
                    self.stream = None
                    with self.lock:
                        self.grabbed = False
                    time.sleep(3.0) # Chờ 3 giây trước khi thử lại
                    continue # Bỏ qua vòng lặp này và thử lại từ đầu

            # Đọc khung hình từ stream đã kết nối
            is_read, frame = self.stream.read()

            # Cập nhật trạng thái và khung hình một cách an toàn
            with self.lock:
                self.grabbed = is_read
                if is_read:
                    self.frame = frame
                else:
                    # Nếu đọc thất bại, có thể camera vừa bị rút ra
                    logging.warning("⚠️ Không thể đọc khung hình, camera có thể đã mất kết nối.")
                    self.stream.release()
                    self.stream = None
    # =================================================================

    def read(self):
        """Trả về khung hình cuối cùng đã đọc được."""
        with self.lock:
            return self.frame

    def is_running(self):
        """Kiểm tra xem camera có đang chạy và đọc được khung hình không."""
        with self.lock:
            return not self.stopped and self.grabbed

    def stop(self):
        """Dừng luồng và giải phóng tài nguyên."""
        self.stopped = True
        if self.stream:
            self.stream.release()