import cv2
import boto3
import threading
import time
import os
from datetime import datetime
from ..utils.logging_utils import get_logger
from ..database.models import AttendanceRecord
from dotenv import load_dotenv
from attendance_system.config.settings import FACE_RECOGNITION_THRESHOLD


logger = get_logger(__name__)
load_dotenv()

class FaceRecognitionProcessor:
    def __init__(self, device_id: str, recognition_interval: int = 5):
        self.device_id = device_id
        self.recognition_interval = recognition_interval  # seconds
        self.last_recognition_times = {}  # To prevent duplicate recognitions
        self.camera = None
        self.is_running = False
        
        # Initialize AWS Rekognition client
        self.rekognition_client = boto3.client('rekognition',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION')
        )
        
        # Define faces directory
        self.faces_directory = "faces/"  # You can make this configurable
        
        self.last_recognition_text = None
        self.text_display_time = None
        self.text_duration = 2  # Duration in seconds

        
    def compare_with_stored_faces(self, frame):
        """Compare captured frame with all faces stored in faces directory"""
        try:
            # Convert frame to bytes for AWS Rekognition
            _, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            
            # Iterate through all images in faces directory
            for face_file in os.listdir(self.faces_directory):
                if not face_file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    continue
                
                # Get enrollment code from filename (without extension)
                enrollment_code = os.path.splitext(face_file)[0]
                
                # Read stored face image
                face_path = os.path.join(self.faces_directory, face_file)
                with open(face_path, 'rb') as stored_face:
                    stored_face_bytes = stored_face.read()
                
                try:
                    # Compare faces using AWS Rekognition
                    response = self.rekognition_client.compare_faces(
                        SourceImage={'Bytes': frame_bytes},
                        TargetImage={'Bytes': stored_face_bytes},
                        SimilarityThreshold=FACE_RECOGNITION_THRESHOLD,
                        QualityFilter='AUTO'
                    )
                    
                    # Check if there's a match
                    if response['FaceMatches']:
                        similarity = response['FaceMatches'][0]['Similarity']
                        logger.info(f"Match found for enrollment code {enrollment_code} with similarity {similarity:.2f}%")
                        return enrollment_code, similarity
                        
                except Exception as e:
                    logger.error(f"Error comparing face with {face_file}: {e}")
                    continue
                    
            return None, 0
            
        except Exception as e:
            logger.error(f"Error in face comparison process: {e}")
            return None, 0

    def handle_recognition(self, enrollment_code, similarity, callback):
        """Handle successful recognition event"""
        current_time = datetime.now()
        
        # Check if enough time has passed since last recognition
        if enrollment_code in self.last_recognition_times:
            time_diff = (current_time - self.last_recognition_times[enrollment_code]).total_seconds()
            if time_diff < self.recognition_interval:
                return
        
        # Update last recognition time
        self.last_recognition_times[enrollment_code] = current_time
        
        # Create attendance record
        attendance_record = AttendanceRecord(
            student_id=enrollment_code, #TODO: fix that later
            device_id=self.device_id,
            capture_timestamp=current_time,
            confidence_score=similarity
        )
        
        # Call the callback function with the attendance record
        callback(attendance_record)

    def process_frame(self, frame, callback):
        """Process a single frame for face recognition"""
        try:
            enrollment_code, similarity = self.compare_with_stored_faces(frame)
            if enrollment_code:
                self.handle_recognition(enrollment_code, similarity, callback)
                # Update text display time and content
                self.last_recognition_text = f"Face Recognized: {enrollment_code}"
                self.text_display_time = time.time()
                
            return enrollment_code
        except Exception as e:
            logger.error(f"Error processing frame: {e}")
            return None


    def start_camera(self, camera_index=0):
        """Start the camera capture"""
        try:
            self.camera = cv2.VideoCapture(camera_index)
            # Reduce resolution for better performance
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            if not self.camera.isOpened():
                raise Exception("Could not open camera")
            logger.info("Camera started successfully")
        except Exception as e:
            logger.error(f"Error starting camera: {e}")
            raise

    def run_recognition(self, callback):
        """Run the face recognition process"""
        self.is_running = True
        last_process_time = time.time()
        
        while self.is_running:
            try:
                ret, frame = self.camera.read()
                if not ret:
                    logger.error("Failed to capture frame")
                    continue
                
                # Process frame at specified interval
                current_time = time.time()
                if (current_time - last_process_time) >= self.recognition_interval:
                    # Process frame and get recognition result
                    recognition_result = self.process_frame(frame.copy(), callback)
                    last_process_time = current_time
                
                # Display text if within duration window
                if self.last_recognition_text and self.text_display_time:
                    if (current_time - self.text_display_time) <= self.text_duration:
                        # Draw a green rectangle around the frame
                        height, width = frame.shape[:2]
                        cv2.rectangle(frame, (0, 0), (width, height), (0, 255, 0), 2)
                        
                        # Display recognition text
                        cv2.putText(frame, self.last_recognition_text, 
                                  (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                                  0.7, (0, 255, 0), 2)
                    else:
                        # Clear the text after duration expires
                        self.last_recognition_text = None
                        self.text_display_time = None
                
                # Show the webcam feed in a window
                cv2.imshow('Smart Check', frame)
                
                # Exit when the 'q' key is pressed
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
                
            except Exception as e:
                logger.error(f"Error in recognition loop: {e}")
                
            # Small delay to prevent excessive CPU usage
            time.sleep(0.1)

    def stop(self):
        """Stop the face recognition process"""
        self.is_running = False
        if self.camera:
            self.camera.release()
        cv2.destroyAllWindows()
        logger.info("Camera stopped and window closed")
