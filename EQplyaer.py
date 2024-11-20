import tkinter as tk
from PIL import Image, ImageTk
import mysql.connector
import numpy as np
from scipy import signal
import threading
import os
import sounddevice as sd
import soundfile as sf
import time
import queue

class EQPlayer:
    def __init__(self):
        self.is_playing = False
        self.sliders = []
        self.categories = []
        self.playback_start_time = 0
        self.total_duration = 0
        self.current_gains = [0, 0, 0, 0, 0]
        self.audio_queue = queue.Queue(maxsize=64)  # 큐 사이즈 충분히 확보
        self.audio_stream = None
        self.current_position = 0  # 현재 재생 위치
        self.audio_data = None     # 전체 오디오 데이터
        self.samplerate = None     # 샘플레이트 저장
        self.buffer_size = 4096  # 버퍼 사이즈 증가
        self.freqs = [100, 300, 1000, 3000, 10000]  # 주파수 대역 정의
        
        self.setup_gui()
        
    def connect_to_db(self):
        return mysql.connector.connect(
            host='192.168.101.227',
            user='Second',
            password='rkdwlsah12!*',
            database='second_pj',
            port=3306
        )

    def peak_filter(self, data, center_freq, fs, gain, Q=1.0):
        nyq = 0.5 * fs
        freq = center_freq / nyq
        b, a = signal.iirpeak(freq, Q)
        return signal.lfilter(b, a, data) * (10**(gain / 20))

    def equalizer(self, data, fs, freqs, gains, Q=1.0):
        try:
            data = np.array(data, dtype=np.float32)
            filtered = np.zeros_like(data)
            for freq, gain in zip(freqs, gains):
                filtered += self.peak_filter(data, freq, fs, gain, Q)
            filtered = filtered / len(freqs)  # 필터 수로 나누어 평균
            return filtered
        except Exception as e:
            print(f"Error in equalizer function: {e}")
            return data

    def update_playback_bar(self):
        if self.is_playing:
            elapsed = time.time() - self.playback_start_time
            percentage = min((elapsed / self.total_duration) * 100, 100)
            self.playback_bar.set(percentage)
            if percentage < 100:
                self.root.after(1000, self.update_playback_bar)

    def load_and_play_audio(self, file_path):
        if self.is_playing:
            self.stop_audio()
        
        # 파일이 바뀌었을 때만 새로 로드
        if not hasattr(self, 'current_file') or self.current_file != file_path:
            try:
                with sf.SoundFile(file_path) as f:
                    self.audio_data = f.read(dtype="float32")
                    if self.audio_data.ndim > 1:
                        self.audio_data = np.mean(self.audio_data, axis=1, dtype=np.float32)
                    self.samplerate = f.samplerate
                    self.total_duration = len(self.audio_data) / self.samplerate
                self.current_file = file_path
                self.current_position = 0  # 새 파일일 경우 위치 초기화
            except Exception as e:
                print(f"Error loading audio file: {e}")
                return

        self.is_playing = True
        self.playback_start_time = time.time() - (self.current_position / self.samplerate)
        threading.Thread(target=self.play_audio_from_position, daemon=True).start()
        self.root.after(100, self.update_playback_bar)

    def play_audio_from_position(self):
        try:
            if self.audio_stream is not None:
                self.audio_stream.stop()
                self.audio_stream.close()
            
            self.audio_stream = sd.OutputStream(
                samplerate=self.samplerate,
                channels=1,
                callback=self.audio_callback,
                blocksize=self.buffer_size,
                dtype=np.float32
            )
            self.audio_stream.start()

            # 저장된 위치부터 재생 시작
            for i in range(self.current_position, len(self.audio_data), self.buffer_size):
                if not self.is_playing:
                    break
                
                chunk = self.audio_data[i:i + self.buffer_size]
                if len(chunk) < self.buffer_size:
                    chunk = np.pad(chunk, (0, self.buffer_size - len(chunk)))
                
                while self.is_playing and self.audio_queue.full():
                    time.sleep(0.001)
                
                self.audio_queue.put(chunk)
                self.current_position = i  # 현재 위치 업데이트

        except Exception as e:
            print(f"Error in audio playback: {e}")
        finally:
            if self.audio_stream is not None:
                self.audio_stream.stop()
                self.audio_stream.close()

    def audio_callback(self, outdata, frames, time_info, status):
        try:
            data = self.audio_queue.get_nowait()
            if any(gain != 0 for gain in self.current_gains):  # gain 값이 변경된 경우에만 이퀄라이저 적용
                # 이퀄라이저 처리 적용
                processed_data = self.equalizer(
                    data,
                    self.samplerate,
                    self.freqs,
                    self.current_gains
                )
                outdata[:] = processed_data.reshape(-1, 1)
            else:
                outdata[:] = data.reshape(-1, 1)
        except queue.Empty:
            outdata.fill(0)
        except Exception as e:
            print(f"Error in audio callback: {e}")
            outdata.fill(0)

    def stop_audio(self):
        if self.is_playing:
            self.is_playing = False
            elapsed = time.time() - self.playback_start_time
            self.current_position = min(
                int(elapsed * self.samplerate),
                len(self.audio_data) if self.audio_data is not None else 0
            )
            
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except queue.Empty:
                    break
            
            if self.audio_stream is not None:
                self.audio_stream.stop()
                self.audio_stream.close()
                self.audio_stream = None
        
        sd.stop()

    def load_categories(self):
        conn = self.connect_to_db()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT GNR_MLSFC_NM FROM watched_data")
        self.categories = [row[0] for row in cursor.fetchall()]
        print("category is : ", self.categories)
        conn.close()

    def load_equalizer_settings(self, category):
        conn = self.connect_to_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT Hz_100, Hz_300, Hz_1k, Hz_3k, Hz_10k
            FROM equalizer_settings
            WHERE E_ID = %s
            LIMIT 1
        """, (category,))
        settings = cursor.fetchone() or [50, 50, 50, 50, 50]

        for i, slider in enumerate(self.sliders):
            slider.set(settings[i])

        print(settings)

        cursor.close()
        conn.close()

        adjusted_settings = [gain - 50 for gain in settings]
        return adjusted_settings

    def apply_category_settings(self):
        if self.categories:
            selected_category = self.categories[0]
            gains = self.load_equalizer_settings(selected_category)
            self.current_gains = gains

    def on_slider_change(self, index, value):
        self.current_gains[index] = int(value) - 50
        print(f"Frequency {self.freqs[index]}Hz: Gain {self.current_gains[index]}dB")

    def load_image(self, filename: str):
        img = Image.open(filename)
        img_tk = ImageTk.PhotoImage(img)
        label = tk.Label(self.root, image=img_tk)
        label.image = img_tk
        label.pack()

    def setup_gui(self):
        self.root = tk.Tk()
        self.root.title("실시간 이퀄라이저 프로그램")
        self.root.geometry("800x600")

        for i, label in enumerate(["100Hz", "300Hz", "1kHz", "3kHz", "10kHz"]):
            slider = tk.Scale(self.root, from_=100, to=0, orient='vertical', 
                            label=label, command=lambda val, idx=i: self.on_slider_change(idx, val))
            slider.set(50)
            slider.pack(side='left', fill='y', expand=True)
            self.sliders.append(slider)

        self.playback_bar = tk.Scale(self.root, from_=0, to=100, orient='horizontal', length=600)
        self.playback_bar.pack(side='top', pady=10)

        play_button = tk.Button(self.root, text="재생", 
                              command=lambda: self.load_and_play_audio("sound/ROSE.mp3"))
        play_button.pack(side='left', padx=10)

        stop_button = tk.Button(self.root, text="정지", command=self.stop_audio)
        stop_button.pack(side='left', padx=10)

        category_button = tk.Button(self.root, text="카테고리 설정 적용", 
                                  command=self.apply_category_settings)
        category_button.pack(side='left', padx=10)

        self.load_image("image/img02.jpg")
        self.load_categories()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    player = EQPlayer()
    player.run()
