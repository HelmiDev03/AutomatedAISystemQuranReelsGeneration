import asyncio
import httpx

async def main():
    async with httpx.AsyncClient() as client:
        # Test Everyayah URL for Surah 1, Ayah 1
        url = "https://everyayah.com/data/Muhammad_Al_Luhaidan_64kbps/001001.mp3"
        r = await client.head(url)
        print(f"Everyayah Luhaidan 64kbps: {r.status_code}")

        url2 = "https://everyayah.com/data/Muhammad_al-Luhaidan_128kbps/001001.mp3"
        r2 = await client.head(url2)
        print(f"Everyayah Luhaidan 128kbps: {r2.status_code}")
        
        # Test alquran.cloud just in case
        r3 = await client.get('https://api.alquran.cloud/v1/edition/format/audio')
        reciters = [e['identifier'] for e in r3.json()['data'] if e['language'] == 'ar']
        print(f"Alquran cloud reciters: {reciters[:5]}")

asyncio.run(main())
