import httpx
import os

url = 'https://upload.wikimedia.org/wikipedia/commons/4/4e/A_drone_video_of_the_landscape_at_a_part_of_Rishikesh.mp4'

if not os.path.exists('backgrounds'):
    os.makedirs('backgrounds')

with httpx.Client(follow_redirects=True) as client:
    r = client.get(url)
    r.raise_for_status()
    with open('backgrounds/sample_nature.mp4', 'wb') as f:
        f.write(r.content)
print("Downloaded background video!")
