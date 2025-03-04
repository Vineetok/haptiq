import cv2
import numpy as np
import datetime
import sys
from pymongo import MongoClient
from gridfs import GridFS

# Connect to MongoDB and set up collections
client = MongoClient('mongodb://localhost:27017/')
db = client['face_recognition']
fs = GridFS(db)
users_collection = db['users']
reports_collection = db['reports']
categories_collection = db['categories']  # Collection storing category data

def generate_blog_report(celebrity_matches):
    if not celebrity_matches:
        print("No matches with confidence over 60%.")
        return

    for match in celebrity_matches:
        print(f"Processing match: {match}")  # Debug print

        try:
            category, filename = match['Celebrity'].split('/')
        except ValueError as e:
            print(f"Error splitting 'Celebrity': {e}. Data: {match['Celebrity']}")
            continue

        # Check if a report for this person already exists (by their image_id/name)
        existing_report = reports_collection.find_one({"matches.Celebrity": filename})
        if existing_report:
            print(f"Report for {filename} already exists in the database.")
            continue

        report_data = {
            "category": category,
            "generated_at": datetime.datetime.now(),
            "matches": [match],
            "report_name": filename
        }
        result = reports_collection.insert_one(report_data)
        print(f"Report stored in MongoDB with ID: {result.inserted_id}")

class FaceRecognition:
    def __init__(self):
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        self.known_face_encodings = []
        self.known_face_names = []
        self.process_current_frame = True
        self.matched_celebrities = set()

    def encode_faces(self):
        # Dynamically fetch all category documents
        category_docs = list(categories_collection.find())
        if not category_docs:
            print("No categories found in the database.")
            return

        for cat in category_docs:
            cat_name = cat.get('name', 'unknown')
            images_loaded = False

            if 'images' in cat and cat['images']:
                for img in cat['images']:
                    file_id = img.get('file_id')
                    if not file_id:
                        continue
                    try:
                        image_file = fs.get(file_id).read()
                    except Exception as e:
                        print(f"Error retrieving file for category '{cat_name}' from images array: {e}")
                        continue

                    face_image = cv2.imdecode(np.frombuffer(image_file, np.uint8), cv2.IMREAD_GRAYSCALE)
                    faces = self.face_cascade.detectMultiScale(face_image, scaleFactor=1.1, minNeighbors=5)
                    if len(faces) > 0:
                        # Crop face region to use as encoding
                        x, y, w, h = faces[0]  # Use the first detected face
                        cropped_face = face_image[y:y+h, x:x+w]
                        self.known_face_encodings.append(cropped_face)
                        self.known_face_names.append(f"{cat_name}/{img.get('filename', file_id)}")
                        images_loaded = True
            else:
                if 'image_id' in cat:
                    try:
                        image_file = fs.get(cat['image_id']).read()
                    except Exception as e:
                        print(f"Error retrieving file for category '{cat_name}' from image_id: {e}")
                        continue

                    face_image = cv2.imdecode(np.frombuffer(image_file, np.uint8), cv2.IMREAD_GRAYSCALE)
                    faces = self.face_cascade.detectMultiScale(face_image, scaleFactor=1.1, minNeighbors=5)
                    if len(faces) > 0:
                        x, y, w, h = faces[0]  # Use the first detected face
                        cropped_face = face_image[y:y+h, x:x+w]
                        self.known_face_encodings.append(cropped_face)
                        self.known_face_names.append(f"{cat_name}/{cat['image_id']}")
                        images_loaded = True

            if not images_loaded:
                print(f"No valid face images loaded for category: {cat_name}")

        print("Known faces loaded:", self.known_face_names)

    def run_recognition(self):
        video_capture = cv2.VideoCapture(0)

        if not video_capture.isOpened():
            sys.exit('Video source not found...')

        video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

        celebrity_matches = []

        while True:
            ret, frame = video_capture.read()
            if not ret or frame is None:
                print("Failed to grab frame.")
                break

            if self.process_current_frame:
                gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.face_cascade.detectMultiScale(gray_frame, scaleFactor=1.1, minNeighbors=5)

                for (x, y, w, h) in faces:
                    roi_gray = gray_frame[y:y + h, x:x + w]

                    # Compare ROI with every known face encoding
                    matches = []
                    for encoding in self.known_face_encodings:
                        # Resize encoding to match roi_gray if larger
                        if encoding.shape[0] > roi_gray.shape[0] or encoding.shape[1] > roi_gray.shape[1]:
                            encoding = cv2.resize(encoding, (roi_gray.shape[1], roi_gray.shape[0]))
                        try:
                            match_score = cv2.matchTemplate(roi_gray, encoding, cv2.TM_CCOEFF_NORMED).max()
                            matches.append(match_score)
                        except cv2.error as e:
                            print(f"Error in matchTemplate: {e}")
                            matches.append(0)  # Default to no match

                    confidence = max(matches) if matches else 0

                    if confidence > 0.4:
                        best_match_index = np.argmax(matches)
                        try:
                            category, image_id = self.known_face_names[best_match_index].split('/')
                        except Exception as e:
                            print(f"Error splitting known face reference: {e}")
                            continue
                        name = image_id

                        if f'{category}/{name}' in self.matched_celebrities:
                            print(f"Report for {category}/{name} already generated, skipping.")
                            continue

                        self.matched_celebrities.add(f'{category}/{name}')

                        # Encode the snapshot to save in MongoDB
                        _, snapshot = cv2.imencode('.jpg', frame)
                        timestamp_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                        snapshot_filename = f'snapshot_{name.replace("/", "")}_{timestamp_str}.jpg'
                        snapshot_id = fs.put(snapshot.tobytes(), filename=snapshot_filename)

                        celebrity_matches.append({
                            'Celebrity': f'{category}/{name}',
                            'Confidence': f"{confidence * 100:.2f}%",
                            'Timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'Image': snapshot_id,
                            'Location': {
                                'x': int(x),
                                'y': int(y),
                                'w': int(w),
                                'h': int(h)
                            }
                        })

                        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                        cv2.putText(frame, f'{name} ({confidence * 100:.2f}%)', (x, y - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2)

            self.process_current_frame = not self.process_current_frame

            cv2.imshow('Face Recognition', frame)
            if cv2.waitKey(1) == ord('q'):
                break

        generate_blog_report(celebrity_matches)

        video_capture.release()
        cv2.destroyAllWindows()

if __name__ == '__main__':
    fr = FaceRecognition()
    fr.encode_faces()
    fr.run_recognition()