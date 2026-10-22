import openpyxl, re, sys, unicodedata

sys.stdout.reconfigure(encoding='utf-8')

input_path = r'c:\Users\Admin\Downloads\automacaoconsultas-main\WhatsApp 2026-8-3 13-0-55.xlsx'
output_path = r'c:\Users\Admin\Downloads\automacaoconsultas-main\Contatos_Limpos_WhatsApp.xlsx'

wb = openpyxl.load_workbook(input_path)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))[1:]

def strip_accents(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def remove_emojis_and_symbols(text):
    res = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith('L') or ch in (' ', '-', "'"):
            res.append(ch)
    return ''.join(res).strip()

def is_concatenated_username(text):
    # e.g. pedropaulomonteiromoura22, luizcarlosdossantoscarlos, adrianadacostacosta1
    # Check if a single long token (>11 chars) with all lowercase or numbers
    raw = text.strip()
    if ' ' not in raw:
        if len(raw) > 12:
            return True
        if re.search(r'\d+$', raw) and len(raw) > 7:
            return True
        if raw.endswith('@gm') or '@' in raw:
            return True
    return False

# Words that indicate business, phrases, greetings, titles, etc.
PHRASE_BUSINESS_TERMS = {
    'compro', 'vendo', 'loja', 'oficina', 'mecanica', 'veiculos', 'veiculo', 'carros', 'carro',
    'fiel', 'amor', 'paz', 'vendas', 'consultoria', 'atendimento',
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
    'qualidade', 'garantia', 'melhor', 'preco', 'orcamento', 'gratis', 'gratuitos', 'zap',
    'familia', 'best', 'xango', 'exu', 'pai', 'filho', 'filha', 'deus'
}

# Real surname exceptions for 'deus' or 'jesus'
# Note: "jesus" is allowed if it's a person's name like "Edson De Jesus Carvalho" or "Giuliano de Jesus"
# "deus" in phrases like "deus e fiel" is NOT allowed.

INVALID_NICKNAMES_OR_HANDLES = {
    'tb', 'mv', 'pc', 'js', 'rpl', 'rcd', 'crf', 'oi', 'claw', 'indio', 'vollat', 'mazin',
    'bigode', 'acreano', 'morena', 'kaos', 'solvaip', 'biahaus', 'osbrito', 'aldoflyy',
    'ph', 'karioka', 'lequinho', 'bn capixaba', 'capixaba', 'guerreira', 'the best'
}

cleaned_records = []
rejected_records = []

for idx, r in enumerate(rows, start=2):
    phone_raw = str(r[0]).strip() if r[0] is not None else ''
    c_name = str(r[1]).strip() if r[1] is not None else ''
    w_name = str(r[2]).strip() if r[2] is not None else ''
    
    # 1. Clean phone (+55)
    phone = phone_raw
    if phone.startswith('+55'):
        phone = phone[3:]
    elif phone.startswith('55') and len(re.sub(r'\D', '', phone)) >= 12:
        phone = phone[2:]
    phone = re.sub(r'\D', '', phone)
    
    if not phone or phone == '0' or len(phone) < 8:
        rejected_records.append((idx, phone_raw, c_name or w_name, 'Número inválido / 0'))
        continue

    # Pick name
    if c_name and c_name.upper() != 'N/A':
        raw_name = c_name
    elif w_name and w_name.upper() != 'N/A':
        raw_name = w_name
    else:
        raw_name = 'N/A'
        
    # 2. N/A or empty
    if not raw_name or raw_name.upper() == 'N/A':
        rejected_records.append((idx, phone, raw_name, 'Nome N/A ou Vazio'))
        continue
        
    # Remove emojis and symbols
    clean_n = remove_emojis_and_symbols(raw_name)
    clean_n = re.sub(r'\s+', ' ', clean_n)
    norm_n = strip_accents(clean_n.lower())
    words = norm_n.split()
    
    # 3. Only emojis / symbols / < 2 letters
    if not clean_n or len(clean_n) < 2 or not words:
        rejected_records.append((idx, phone, raw_name, 'Apenas emojis/símbolos ou sem letras'))
        continue
        
    # 4. Incomprehensible concatenated username
    if is_concatenated_username(clean_n):
        rejected_records.append((idx, phone, raw_name, f'Incompreensível / Nome de usuário concatenado ("{clean_n}")'))
        continue
        
    # 5. Non-person nickname / handle check
    if norm_n in INVALID_NICKNAMES_OR_HANDLES:
        rejected_records.append((idx, phone, raw_name, f'Apelido que não remete a nome verdadeiro ("{raw_name}")'))
        continue
        
    # Check individual words for non-name handles
    if len(words) == 1 and words[0] in INVALID_NICKNAMES_OR_HANDLES:
        rejected_records.append((idx, phone, raw_name, f'Apelido que não remete a nome verdadeiro ("{raw_name}")'))
        continue
        
    # 6. Phrases / Slogans / Non-person terms
    # Check for religious phrases or business phrases
    is_phrase_or_business = False
    bad_reason = ''
    
    for w in words:
        if w in PHRASE_BUSINESS_TERMS:
            # Exception check for 'jesus' or 'deus' when part of a person's full name
            if w == 'deus':
                is_phrase_or_business = True
                bad_reason = f'Frase/slogan ("{raw_name}")'
                break
            elif w in ('deus', 'familia', 'xango', 'exu', 'pai', 'filho', 'filha'):
                is_phrase_or_business = True
                bad_reason = f'Frase/slogan ("{raw_name}")'
                break
            else:
                is_phrase_or_business = True
                bad_reason = f'Termo comercial/frase ("{w}")'
                break
                
    if is_phrase_or_business:
        rejected_records.append((idx, phone, raw_name, bad_reason))
        continue
        
    if len(words) > 4:
        rejected_records.append((idx, phone, raw_name, f'Frase longa ({len(words)} palavras)'))
        continue

    # Format output name: capitalize words properly if needed, or keep cleaned version
    formatted_name = ' '.join(w.capitalize() for w in clean_n.split())
    cleaned_records.append((formatted_name, phone, raw_name))

print(f'Final Limpos: {len(cleaned_records)}')
print(f'Final Rejeitados: {len(rejected_records)}')

# Create new Workbook
out_wb = openpyxl.Workbook()
out_ws = out_wb.active
out_ws.title = 'Contatos'

# Header
out_ws.append(['Nome do contato', 'Número'])

for name, phone, _ in cleaned_records:
    out_ws.append([name, phone])

out_wb.save(output_path)
print(f'Salvo com sucesso em: {output_path}')
