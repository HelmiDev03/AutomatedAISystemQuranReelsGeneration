import urllib.request, urllib.error
import json

req = urllib.request.Request(
    'https://graph.instagram.com/v22.0/36334782392834296/media?access_token=IGAAOH7tuhCJFBZAFowQWYzNUFRdzlwUUVVNmloR0Q1U0ZA6STE5N2NsNnZAoaEc3bjJqeWhXX1oxeFpxM2d4dVN6ajNTSUtSVUpHM1BoSmNMVGh6dUFoRnp3ci1iV0EzZA2RxT25wbmNoOXZAsTFFjcmJnWXg2Y0FMT2RhN0pRaWtzcwZDZD', 
    method='POST'
)
try:
    res = urllib.request.urlopen(req)
    print(res.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
