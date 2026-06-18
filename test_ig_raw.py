import asyncio
import httpx
from app.config import Settings

async def test():
    settings = Settings()
    business_id = "17841478280872429"
    url = f"https://graph.instagram.com/v22.0/{business_id}/media"
    
    # Also test facebook graph url to see if that works
    url_fb = f"https://graph.facebook.com/v22.0/{business_id}/media"
    
    payload = {
        "access_token": settings.instagram_access_token,
        "media_type": "REELS",
        "video_url": "https://res.cloudinary.com/dmvxysqvl/video/upload/v1781805312/islamic-content/hco6cgzf4exvaymdjjdp.mp4",
        "caption": "Test caption",
        "share_to_feed": "true"
    }
    
    async with httpx.AsyncClient() as client:
        print("Testing graph.instagram.com URL:")
        try:
            response = await client.post(url, data=payload)
            print(f"Status: {response.status_code}")
            print(f"Headers: {dict(response.headers)}")
            print(f"Body: {response.text}")
        except Exception as e:
            print(f"Failed: {e}")
            
        print("\nTesting graph.facebook.com URL:")
        try:
            response = await client.post(url_fb, data=payload)
            print(f"Status: {response.status_code}")
            print(f"Headers: {dict(response.headers)}")
            print(f"Body: {response.text}")
        except Exception as e:
            print(f"Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
