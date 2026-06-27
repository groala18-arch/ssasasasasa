import os
from aiohttp import web

async def test_handler(request):
    return web.Response(text="✅ СЕРВЕР ЖИВ! Ошибки 502 больше нет. Проблема была в логике игры или базе данных.", content_type='text/html')

app = web.Application()
app.router.add_get('/', test_handler)

if __name__ == '__main__':
    print("Запуск тестового сервера...")
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host='0.0.0.0', port=port)
