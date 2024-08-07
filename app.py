from flask import Flask, request, send_file, render_template, jsonify
import cv2
import os
import zipfile
import uuid
import tempfile
import threading
import time
import random

app = Flask(__name__)

# Global dictionary to store processing progress and settings
processing_progress = {}
processing_settings = {}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file uploaded'}), 400
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if file:
        try:
            unique_id = uuid.uuid4().hex
            temp_dir = tempfile.mkdtemp()
            file_path = os.path.join(temp_dir, file.filename)
            file.save(file_path)

            # Get video info
            fps, total_frames, duration = get_video_info(file_path)

            # Store file information
            processing_settings[unique_id] = {
                'file_path': file_path,
                'temp_dir': temp_dir,
                'filename': file.filename,
                'fps': fps,
                'total_frames': total_frames,
                'duration': duration
            }

            return jsonify({
                'id': unique_id,
                'filename': file.filename,
                'maxFps': fps
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 50

def get_video_info(video_path):
    cap = cv2.VideoCapture(video_path)
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    cap.release()
    return fps, total_frames, duration


@app.route('/process', methods=['POST'])
def process_video():
    data = request.json
    unique_id = data.get('id')
    frames_per_second = data.get('framesPerSecond', 1)
    shuffle = data.get('shuffle', False)

    if unique_id not in processing_settings:
        return jsonify({'error': 'Invalid ID'}), 400

    settings = processing_settings[unique_id]
    fps, total_frames, duration = get_video_info(settings['file_path'])
    settings['frames_per_second'] = min(frames_per_second, fps)
    settings['shuffle'] = shuffle
    settings['fps'] = fps
    settings['total_frames'] = total_frames
    settings['duration'] = duration

    # Start processing in a separate thread
    threading.Thread(target=extract_frames, args=(unique_id,)).start()

    return jsonify({'message': 'Processing started', 'maxFps': fps})


def extract_frames(unique_id):
    settings = processing_settings[unique_id]
    processing_progress[unique_id] = 0

    try:
        cap = cv2.VideoCapture(settings['file_path'])
        fps = settings['fps']
        total_frames = settings['total_frames']
        duration = settings['duration']
        frames_per_second = settings['frames_per_second']
        shuffle = settings['shuffle']

        frame_indices = []
        for second in range(int(duration)):
            second_start_frame = second * fps
            second_end_frame = min((second + 1) * fps, total_frames)
            frames_this_second = list(range(second_start_frame, second_end_frame))

            if shuffle:
                selected_frames = random.sample(frames_this_second, min(frames_per_second, len(frames_this_second)))
            else:
                selected_frames = frames_this_second[:frames_per_second]

            frame_indices.extend(selected_frames)

        total_frames_to_extract = len(frame_indices)
        saved_frames = []

        for i, frame_index in enumerate(frame_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, frame = cap.read()
            if ret:
                frame_path = os.path.join(settings['temp_dir'], f'frame_{frame_index:08d}.png')
                cv2.imwrite(frame_path, frame)
                saved_frames.append(frame_path)

            processing_progress[unique_id] = int((i + 1) / total_frames_to_extract * 100)

        cap.release()

        zip_filename = f"{os.path.splitext(settings['filename'])[0]}_frames.zip"
        zip_path = create_zip(settings['temp_dir'], zip_filename, saved_frames)
        processing_progress[unique_id] = 100

        # Schedule cleanup
        cleanup_thread = threading.Thread(target=cleanup, args=(settings['temp_dir'], zip_path))
        cleanup_thread.start()

    except Exception as e:
        processing_progress[unique_id] = -1
        print(f"Error during video processing: {e}")


def create_zip(temp_dir, zip_filename, files):
    zip_path = os.path.join(temp_dir, zip_filename)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for file in files:
            zipf.write(file, os.path.basename(file))
    return zip_path


def cleanup(temp_dir, zip_path):
    time.sleep(300)  # Wait for 5 minutes before cleanup
    try:
        os.remove(zip_path)
        for root, dirs, files in os.walk(temp_dir, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
        os.rmdir(temp_dir)
    except Exception as e:
        print(f"Error during cleanup: {e}")


@app.route('/progress/<unique_id>')
def progress(unique_id):
    return jsonify({'progress': processing_progress.get(unique_id, 0)})


@app.route('/download/<unique_id>')
def download_file(unique_id):
    if unique_id not in processing_progress or processing_progress[unique_id] != 100:
        return jsonify({'error': 'File not ready or does not exist'}), 400

    settings = processing_settings.get(unique_id)
    if not settings:
        return jsonify({'error': 'Settings not found'}), 404

    zip_filename = f"{os.path.splitext(settings['filename'])[0]}_frames.zip"
    zip_path = os.path.join(settings['temp_dir'], zip_filename)

    if not os.path.exists(zip_path):
        return jsonify({'error': 'Zip file not found'}), 404

    return send_file(zip_path, as_attachment=True, download_name=zip_filename)


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0')