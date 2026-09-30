"""
Descobre o número (chat_id) do canal ou grupo do Telegram.

Antes de rodar:
  1. Adicione o bot como ADMINISTRADOR do canal (ou membro do grupo).
  2. Escreva qualquer mensagem no canal/grupo (ex.: "oi").

Depois rode no terminal:
    python ferramentas/descobrir_chat_id.py SEU_TOKEN_DO_BOT
"""
import sys

import requests

if len(sys.argv) < 2:
    sys.exit("Uso: python ferramentas/descobrir_chat_id.py SEU_TOKEN_DO_BOT")

r = requests.get(f"https://api.telegram.org/bot{sys.argv[1]}/getUpdates", timeout=30).json()
if not r.get("ok"):
    sys.exit(f"Token inválido? Resposta do Telegram: {r}")

achados = {}
for u in r.get("result", []):
    for campo in ("channel_post", "message", "my_chat_member", "edited_channel_post"):
        chat = (u.get(campo) or {}).get("chat")
        if chat:
            achados[chat["id"]] = f'{chat.get("title") or chat.get("username") or ""} ({chat.get("type")})'

if not achados:
    print("Nada encontrado. Confira se o bot é administrador e mande uma mensagem nova no canal.")
for cid, nome in achados.items():
    print(f"chat_id = {cid}   ->  {nome}")
