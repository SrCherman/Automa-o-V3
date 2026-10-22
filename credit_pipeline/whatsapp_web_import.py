"""
Extrai conversas do WhatsApp Web via Edge aberto na porta 9222.

Objetivo:
  Capturar nome + telefone de conversas antigas, inclusive quando os contatos
  nao estao salvos no dispositivo.

Uso:
  1. Abra o Edge da automacao.
  2. Acesse https://web.whatsapp.com e deixe conectado.
  3. Rode: python whatsapp_web_import.py

Este script usa a sessao humana aberta no navegador. Nao faz bypass.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from DrissionPage import ChromiumOptions, ChromiumPage

from app.services.planilha import adicionar_contatos_em_lote, normalizar_telefone


WHATSAPP_URL = "https://web.whatsapp.com"
MAX_SCROLLS = 800
SCROLL_PAUSE = 0.35
PROFILE_PAUSE = 0.7
STABLE_LIMIT = 28

PHONE_RE = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?(?:9?\d{4})[-.\s]?\d{4}")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
NOISE_RE = re.compile(
    r"^(online|digitando|agora|ontem|yesterday|visto por ultimo|last seen|mensagem|audio|foto|video)$",
    re.I,
)


@dataclass
class CapturedContact:
    nome: str
    telefone: str


def connect_browser() -> ChromiumPage:
    co = ChromiumOptions()
    co.set_local_port(9223)
    return ChromiumPage(co)


def get_whatsapp_tab(page: ChromiumPage):
    for tab_id in page.get_tabs():
        tab = page.get_tab(tab_id)
        if "web.whatsapp.com" in tab.url:
            return tab
    return page.new_tab(WHATSAPP_URL)


def wait_whatsapp_ready(tab):
    tab.get(WHATSAPP_URL)
    print("[WA WEB] Aguardando WhatsApp Web carregar. Se pedir QR, conecte manualmente.")
    for _ in range(180):
        try:
            body = tab.ele("tag:body", timeout=2)
            text = (body.text or "").lower()
        except Exception:
            text = ""
        if "conversas" in text or "chats" in text or "pesquisar" in text or "search" in text or "whatsapp" in text:
            print("[WA WEB] WhatsApp Web carregado.")
            return
        time.sleep(1)
    print("[WA WEB] Nao consegui confirmar carregamento; continuando.")


def normalize_name(text: str) -> str:
    text = " ".join(str(text or "").split())
    text = re.sub(r"\s+\d{1,2}:\d{2}\s*$", "", text).strip()
    return text[:120] or "Desconhecido"


def extract_phone(text: str) -> str:
    for match in PHONE_RE.finditer(text or ""):
        telefone = normalizar_telefone(match.group(0))
        if len(telefone) in (10, 11):
            return telefone
    return ""


def is_bad_name(name: str) -> bool:
    cleaned = normalize_name(name)
    low = cleaned.lower()
    return (
        not cleaned
        or cleaned == "Desconhecido"
        or len(cleaned) < 2
        or cleaned in {".", "-", "|"}
        or TIME_RE.fullmatch(cleaned) is not None
        or NOISE_RE.fullmatch(low) is not None
        or extract_phone(cleaned) != ""
    )


def first_useful_line(text: str) -> str:
    for raw in (text or "").splitlines():
        line = normalize_name(raw)
        if not is_bad_name(line):
            return line
    return "Desconhecido"


def get_visible_chat_rows(tab) -> list[dict]:
    data = tab.run_js(
        """
        const candidates = Array.from(document.querySelectorAll('[role="listitem"], [role="row"]'));
        const visible = candidates.filter(el => {
          const rect = el.getBoundingClientRect();
          const text = (el.innerText || '').trim();
          return text && rect.height > 24 && rect.width > 160 && rect.top >= 0 && rect.top < window.innerHeight;
        });
        return visible.map((el, index) => ({
          index,
          text: el.innerText || '',
          key: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 220)
        }));
        """
    )
    return data or []


def click_visible_row(tab, index: int) -> bool:
    return bool(tab.run_js(
        """
        const index = arguments[0];
        const candidates = Array.from(document.querySelectorAll('[role="listitem"], [role="row"]'));
        const visible = candidates.filter(el => {
          const rect = el.getBoundingClientRect();
          const text = (el.innerText || '').trim();
          return text && rect.height > 24 && rect.width > 160 && rect.top >= 0 && rect.top < window.innerHeight;
        });
        const row = visible[index];
        if (!row) return false;
        row.scrollIntoView({block: 'center'});
        row.click();
        return true;
        """,
        index
    ))


def open_contact_profile(tab):
    tab.run_js(
        """
        const clickables = Array.from(document.querySelectorAll('header, button, [role="button"], [data-testid], span[title]'));
        const candidates = clickables.filter(el => {
          const text = ((el.getAttribute('aria-label') || el.title || el.innerText || '') + '').toLowerCase();
          return text.includes('info') || text.includes('inform') || text.includes('dados') || text.includes('perfil') || text.includes('contact');
        });
        const byHeader = document.querySelector('#main header, main header, header');
        if (byHeader) byHeader.click();
        if (candidates[0]) candidates[0].click();
        """
    )
    time.sleep(PROFILE_PAUSE)


def close_contact_profile(tab):
    tab.run_js(
        """
        const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
        const close = buttons.find(b => {
          const label = ((b.getAttribute('aria-label') || b.title || b.innerText || '') + '').toLowerCase();
          return label.includes('fechar') || label.includes('close') || label.includes('back') || label.includes('voltar');
        });
        if (close) close.click();
        else document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true}));
        """
    )
    time.sleep(0.25)


def read_profile_data(tab) -> dict:
    return tab.run_js(
        """
        const side = document.querySelector(
          '[data-testid="contact-info-drawer"], [role="complementary"], [aria-label*="dados"], [aria-label*="info"], [aria-label*="perfil"], [aria-label*="contact"]'
        );
        const main = document.querySelector('#main');
        const header = main?.querySelector('header');
        const sideText = side?.innerText || '';
        const headerText = header?.innerText || '';
        const titleCandidates = [
          side?.querySelector('h1')?.innerText,
          side?.querySelector('h2')?.innerText,
          side?.querySelector('[dir="auto"]')?.innerText,
          header?.querySelector('[dir="auto"]')?.innerText,
          headerText.split('\\n')[0],
        ].filter(Boolean);
        return {
          title: titleCandidates[0] || '',
          headerText,
          sideText,
          bodyText: document.body.innerText || ''
        };
        """
    ) or {}


def extract_open_chat_contact(tab, row_text: str) -> Optional[CapturedContact]:
    open_contact_profile(tab)
    data = read_profile_data(tab)
    close_contact_profile(tab)

    blobs = [
        str(data.get("title") or ""),
        str(data.get("headerText") or ""),
        str(data.get("sideText") or ""),
        str(data.get("bodyText") or ""),
        row_text or "",
    ]
    combined = "\n".join(blobs)
    telefone = extract_phone(combined)
    if not telefone:
        return None

    nome = first_useful_line(str(data.get("title") or ""))
    if is_bad_name(nome):
        nome = first_useful_line(str(data.get("sideText") or ""))
    if is_bad_name(nome):
        nome = first_useful_line(row_text)
    if is_bad_name(nome):
        return None

    return CapturedContact(nome=nome, telefone=telefone)


def scroll_chat_list(tab):
    tab.run_js(
        """
        const candidates = Array.from(document.querySelectorAll('div[role="grid"], div[aria-label], div[tabindex="0"]'));
        const scrollables = candidates
          .filter(el => el.scrollHeight > el.clientHeight + 100)
          .sort((a, b) => b.scrollHeight - a.scrollHeight);
        const el = scrollables[0] || document.scrollingElement;
        el.scrollTop = el.scrollTop + Math.max(600, el.clientHeight * 0.9);
        return {top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight};
        """
    )


def main():
    page = connect_browser()
    tab = get_whatsapp_tab(page)
    wait_whatsapp_ready(tab)

    seen: dict[str, CapturedContact] = {}
    processed_keys = set()
    stable_rounds = 0
    last_count = 0

    print("[WA WEB] Abrindo conversas/perfis para extrair nome + telefone...")
    for _ in range(MAX_SCROLLS):
        rows = get_visible_chat_rows(tab)
        if not rows:
            scroll_chat_list(tab)
            time.sleep(SCROLL_PAUSE)
            continue

        for row in rows:
            key = row.get("key") or ""
            if not key or key in processed_keys:
                continue
            processed_keys.add(key)

            if not click_visible_row(tab, int(row["index"])):
                continue

            time.sleep(0.7)
            contact = extract_open_chat_contact(tab, row.get("text") or "")
            if contact:
                seen[contact.telefone] = contact
                print(f"[WA WEB] OK: {contact.nome} | {contact.telefone} | total={len(seen)}")
            else:
                print(f"[WA WEB] Ignorado: sem nome+telefone no perfil | {key[:90]}")

        if len(seen) == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_count = len(seen)
            print(f"[WA WEB] Capturados ate agora: {len(seen)}")

        if stable_rounds >= STABLE_LIMIT:
            break

        scroll_chat_list(tab)
        time.sleep(SCROLL_PAUSE)

    contatos = [{"nome": c.nome, "telefone": c.telefone} for c in seen.values()]
    resultado = adicionar_contatos_em_lote(contatos)
    print(f"[WA WEB] Total capturado: {len(contatos)}")
    print(f"[PLANILHA] {resultado}")


if __name__ == "__main__":
    main()
