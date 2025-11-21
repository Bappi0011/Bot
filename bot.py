import logging
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from config import BOT_TOKEN

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

@dp.message_handler(commands=['start'])
async def send_start_message(message: types.Message):
    await message.answer("Welcome to the Meme Caller Bot!", reply_markup=generate_buttons())

if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)