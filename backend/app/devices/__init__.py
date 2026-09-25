"""Device workers that run beside the API on the table machine.

- camera.py: one per webcam — face recognition (who's on this side) and pose-based
  swing detection (who hit), posting to /api/capture/*.
- sensor.py: the table contact sensor — posts one tick per ball contact.
- enroll.py: add a player's face to the gallery from a camera or photos.
- models.py: fetches and verifies the pretrained model files.
"""
