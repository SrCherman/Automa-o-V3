import openpyxl, re, sys, unicodedata

sys.stdout.reconfigure(encoding='utf-8')

wb = openpyxl.load_workbook(r'c:\Users\Admin\Downloads\automacaoconsultas-main\WhatsApp 2026-8-3 13-0-55.xlsx')
ws = wb.active
rows = list(ws.iter_rows(values_only=True))[1:]

all_contacts = []
for idx, r in enumerate(rows, start=2):
    phone_raw = str(r[0]).strip() if r[0] is not None else ''
    c_name = str(r[1]).strip() if r[1] is not None else ''
    w_name = str(r[2]).strip() if r[2] is not None else ''
    
    # Remove +55 or 55 at start
    phone = phone_raw
    if phone.startswith('+55'):
        phone = phone[3:]
    elif phone.startswith('55') and len(re.sub(r'\D', '', phone)) >= 12:
        phone = phone[2:]
    phone = re.sub(r'\D', '', phone)
    
    # Pick effective name: preference to Contact Name if not N/A, else WhatsApp Name
    if c_name and c_name.upper() != 'N/A':
        name = c_name
    elif w_name and w_name.upper() != 'N/A':
        name = w_name
    else:
        name = 'N/A'
        
    all_contacts.append({
        'row': idx,
        'phone_raw': phone_raw,
        'phone': phone,
        'name': name,
        'c_name': c_name,
        'w_name': w_name
    })

def strip_accents(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def remove_emojis_and_symbols(text):
    # Keep only letters, spaces, hyphens
    res = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith('L') or ch == ' ':
            res.append(ch)
    return ''.join(res).strip()

def clean_name(name):
    # Remove emojis and extra spaces
    clean = remove_emojis_and_symbols(name)
    clean = re.sub(r'\s+', ' ', clean)
    return clean

# Known terms that indicate non-names (businesses, phrases, greetings, titles, etc.)
NON_NAME_TERMS = {
    'compro', 'vendo', 'loja', 'oficina', 'mecanica', 'veiculos', 'veiculo', 'carros', 'carro',
    'deus', 'jesus', 'fiel', 'fé', 'fe', 'amor', 'vida', 'paz', 'vendas', 'consultoria', 'atendimento',
    'suporte', 'financeiro', 'comercial', 'advocacia', 'advogado', 'frete', 'transporte',
    'studio', 'beleza', 'salao', 'estetica', 'barbearia', 'pizzaria', 'restaurante', 'mkt',
    'digital', 'gerente', 'auto', 'pecas', 'servicos', 'servico', 'modas', 'boutique',
    'entregas', 'express', 'deposito', 'supermercado', 'farmacia', 'drogaria', 'contabilidade',
    'construtora', 'serralheria', 'marcenaria', 'grafica', 'escolar', 'curso', 'academia',
    'clinica', 'odonto', 'vet', 'pet', 'petshop', 'taxi', 'uber', 'driver', 'moto', 'motoboy',
    'guincho', 'reboque', 'chaveiro', 'lava', 'jato', 'doces', 'bolos', 'buffet', 'eventos',
    'fotografia', 'fotos', 'dj', 'som', 'iluminacao', 'locacao', 'aluguel', 'venda', 'reforma',
    'pintura', 'eletricista', 'encanador', 'gesso', 'pisos', 'vidracaria', 'seguros', 'credito',
    'financiamento', 'emprestimo', 'consorcio', 'multas', 'despachante', 'cartorio', 'engenharia',
    'arquitetura', 'design', 'agencia', 'turismo', 'viagens', 'pousada', 'hotel', 'horti',
    'fruti', 'acougue', 'padaria', 'confeitaria', 'sorveteria', 'lanchonete', 'hamburgueria',
    'pastelaria', 'espetinho', 'bar', 'distribuidora', 'adega', 'bebidas', 'gas', 'agua',
    'associacao', 'sindicato', 'igreja', 'paroquia', 'ministerio', 'pastor', 'grupo', 'equipe',
    'canal', 'page', 'oficial', 'oi', 'ola', 'hi', 'hello', 'ok', 'tudo', 'bem', 'obrigado',
    'obrigada', 'sim', 'nao', 'top', 'show', 'bom', 'dia', 'tarde', 'noite', 'casa', 'ap',
    'apartamento', 'terreno', 'lote', 'sitio', 'chacara', 'fazenda', 'cliente', 'contato',
    'multimarcas', 'motors', 'center', 'mecanico', 'pintor', 'funilaria', 'lanternagem',
    'estofados', 'higienizacao', 'polimento', 'solar', 'energia', 'vendedor', 'vendedora',
    'representante', 'corretor', 'corretora', 'desconto', 'oferta', 'promocao', 'promocional',
    'qualidade', 'garantia', 'melhor', 'preco', 'orcamento', 'gratis', 'gratuitos', 'zap'
}

rejected = []
accepted = []

for c in all_contacts:
    raw_name = c['name']
    cleaned = clean_name(raw_name)
    cleaned_norm = strip_accents(cleaned.lower())
    words = cleaned_norm.split()
    
    # Check 1: N/A or empty
    if not raw_name or raw_name.upper() == 'N/A' or not cleaned:
        rejected.append((c, 'N/A, Vazio ou apenas Emojis/Símbolos'))
        continue
        
    # Check 2: Less than 2 letters overall
    if len(cleaned) < 2:
        rejected.append((c, 'Nome muito curto ou sem letras'))
        continue
        
    # Check 3: Phrases / business terms / greetings
    has_bad_term = False
    bad_term_found = ''
    for w in words:
        if w in NON_NAME_TERMS:
            has_bad_term = True
            bad_term_found = w
            break
            
    if has_bad_term:
        rejected.append((c, f'Termo comercial/frase: "{bad_term_found}"'))
        continue
        
    # Check 4: Phrase check (> 4 words is likely a sentence/phrase)
    if len(words) > 4:
        rejected.append((c, f'Frase longa ({len(words)} palavras)'))
        continue
        
    # Check 5: Gibberish or random nick (single letter/initial or no vowels or weird patterns)
    # Check if a single word name has no vowels or is something like "TB", "X", "Z", etc.
    if len(words) == 1:
        w0 = words[0]
        # Single-letter or 2-letter nickname that isn't a common short name (like Zé, Edu, Leo, Teo, Lia, Bia, Ney)
        if len(w0) <= 2 and w0 not in {'ze', 'edu', 'leo', 'teo', 'lia', 'bia', 'ney', 'lu', 'ge', 'fe', 'ca', 'ju', 'va', 'di'}:
            rejected.append((c, f'Apelido muito curto/sigla: "{raw_name}"'))
            continue
        # No vowels (e.g. "brg", "xpt")
        if not any(v in w0 for v in 'aeiouy'):
            rejected.append((c, f'Nome sem vogais/incompreensível: "{raw_name}"'))
            continue
            
    accepted.append((c, cleaned))

print(f'TOTAL: {len(all_contacts)}')
print(f'ACEITOS: {len(accepted)}')
print(f'REJEITADOS: {len(rejected)}')

with open('accepted_list.txt', 'w', encoding='utf-8') as f:
    for item, cl in accepted:
        f.write(f"Row {item['row']}\tPhone: {item['phone']}\tOriginal: {item['name']}\tCleaned: {cl}\n")

with open('rejected_list.txt', 'w', encoding='utf-8') as f:
    for item, reason in rejected:
        f.write(f"Row {item['row']}\tPhone: {item['phone']}\tName: {item['name']}\tReason: {reason}\n")
