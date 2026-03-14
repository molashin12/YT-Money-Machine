import asyncio
from aiogram import Bot

async def main():
    bot = Bot(token='8305191979:AAG_jcSCOYtIcgOxNtwAS0QB54WZIcdnxro')
    
    # Drop all stuck pending updates (the 18 waiting messages that are timing out)
    print("Deleting current webhook & dropping pending updates...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Re-register the webhook so Telegram hits the new IP address behind vidzyme.digital
    print("Setting new webhook...")
    await bot.set_webhook(
        "https://vidzyme.digital/webhook/telegram",
        drop_pending_updates=True
    )
    
    # Verify the new webhook
    info = await bot.get_webhook_info()
    print("Webhook Info:", info)
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
