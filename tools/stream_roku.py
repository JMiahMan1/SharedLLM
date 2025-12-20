import requests

ROKU_IP = "192.168.2.166:8060"
MEDIA_URL = "http://192.168.2.211:11435/cast_video/1c504eb5640b.mp4"
PLAY_ON_ROKU_APP_ID = 15985

def launch_play_on_roku(roku_ip, media_url):
    launch_url = f"http://{roku_ip}/launch/{PLAY_ON_ROKU_APP_ID}"
    response = requests.post(launch_url)
    response.raise_for_status()
    print(f"Launched Play On Roku (app id {PLAY_ON_ROKU_APP_ID}) on Roku device at {roku_ip}")
    print(f"NOTE: You must manually select the media inside the app. Direct streaming URL launch is not supported.")
    print(f"Media URL: {media_url}")

def main():
    try:
        launch_play_on_roku(ROKU_IP, MEDIA_URL)
    except requests.HTTPError as e:
        print(f"Failed to launch app: {e}")

if __name__ == "__main__":
    main()

