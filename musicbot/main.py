from pyrogram import Client
from dotenv import load_dotenv
import os
load_dotenv()
API_ID=int(os.getenv('API_ID','0'))
API_HASH=os.getenv('API_HASH','')
BOT_TOKEN=os.getenv('BOT_TOKEN','')
app=Client('bot', api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

if __name__=='__main__':
    app.run()
