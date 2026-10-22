# Automação V3 🤖💳

Sistema completo de automação inteligente para enriquecimento de contatos, validação cadastral e simulação de crédito automatizada em portais financeiros.

---

## 📋 Visão Geral

O fluxo atualizado de enriquecimento obrigatório por CPF e consulta independente ao C6 está descrito em [docs/FLUXO_C6.md](docs/FLUXO_C6.md).

O **Automação V3** orquestra a leitura de contatos, o enriquecimento obrigatório por CPF no Mind7 e consultas independentes no Itaú Credline e C6 Auto, com interface web em tempo real.

### Principais Recursos

- **Higienização Inteligente de Contatos**: Algoritmos de normalização fonética, remoção de ruídos em nomes do WhatsApp, tratamento de apelidos e validação estrita de números telefônicos.
- **Automação RPA Resiliente**: Integração com portais via **DrissionPage**, suporte a anti-captcha, múltiplos workers concorrentes e reconexão automática.
- **Painel Web em Tempo Real**: Interface interativa servida via **FastAPI** com streaming de logs via **WebSockets**, gráficos de progresso e gerenciamento de campanhas.
- **Auditoria e Segurança**: Registro detalhado de logs em SQLite com rastreabilidade de cada lead processado.
- **Execução Simples em Windows**: Scripts de inicialização rápida (.bat) prontos para ambiente corporativo.

---

## 🏗️ Arquitetura do Projeto

```text
├── credit_pipeline/        # Scripts e pipelines de enriquecimento de crédito
├── src/
│   ├── api/                # Rotas da API REST (FastAPI) e WebSockets
│   ├── automation/         # Bots de automação RPA (Mind7 e Credline Itaú)
│   ├── database/           # Modelos SQLAlchemy e gerador de sessões SQLite
│   ├── ingestion/          # Regras de normalização, limpeza e sanitização
│   ├── services/           # Orquestradores de campanhas e execução em lote
│   ├── static/             # Interface web do painel (HTML5, Tailwind, JS)
│   ├── utils/              # Módulos utilitários (loggers, helpers)
│   ├── app.py              # Ponto de entrada da aplicação FastAPI
│   └── config.py           # Configurações globais e leitura de ambiente
├── tests/                  # Suíte de testes automatizados (pytest)
├── installer/              # Scripts utilitários de instalação
├── Iniciar_Automacao.bat   # Script para inicializar a automação completa
├── Iniciar_Produto.bat     # Script para subir o painel web localmente
├── requirements.txt        # Dependências Python do projeto
├── pytest.ini             # Configuração da suíte de testes
└── .env.example            # Modelo das variáveis de ambiente necessárias
```

---

## 🚀 Como Começar

### Pré-requisitos

- **Python 3.10** ou superior instalado no sistema.
- **Google Chrome** ou **Microsoft Edge** instalado (necessário para o DrissionPage).
- *(Opcional)* Node.js para os scripts auxiliares do WhatsApp Bridge caso aplicável.

### 1. Clonar o Repositório

```bash
git clone https://github.com/SrCherman/Automa-o-V3.git
cd Automa-o-V3
```

### 2. Criar e Ativar o Ambiente Virtual

No Windows (PowerShell):
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Instalar Dependências

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configurar as Variáveis de Ambiente

Copie o arquivo .env.example para .env:
```powershell
Copy-Item .env.example .env
```

Abra o arquivo .env e configure suas credenciais:
```dotenv
# Portais de Consulta
MIND7_USER=seu_email@exemplo.com
MIND7_PASS=sua_senha
CREDLINE_USER=seu_usuario@exemplo.com
CREDLINE_PASS=sua_senha

# Configurações do Navegador / Workers
HEADLESS=false
MAX_WORKERS=2
```

> ⚠️ **Atenção**: O arquivo .env contém credenciais e jamais deve ser compartilhado ou commitado no repositório.

---

## 💻 Como Executar

### Opção 1: Via Scripts Rápidos (Windows)

- Para abrir o painel web e iniciar o serviço:
  Dê um duplo clique em Iniciar_Produto.bat ou Iniciar_Automacao.bat.

### Opção 2: Via Terminal

```bash
uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload
```
Acesse o painel no navegador: **http://localhost:8000**

---

## 🧪 Testes Automatizados

Os testes locais cobrem regras de negócio, enriquecimento, consultas independentes, retomada e preservação dos dados:

```bash
python -m pytest tests -q -o addopts=
```

---

## 🛡️ Segurança e LGPD

- Assegure-se de que planilhas e relatórios contendo dados sensíveis de pessoas físicas (CPF, telefone, nomes) sejam armazenados em local seguro e em conformidade com a Lei Geral de Proteção de Dados (LGPD).
- O .gitignore deste repositório vem pré-configurado para não versionar planilhas (.xlsx, .csv), bases de dados SQLite e sessões de cookies locais.

O teste manual legado `credit_pipeline/test_simulacao.py` exige a variável de ambiente `TEST_CPF`, sem documento pessoal gravado no código.

---

## 📄 Licença

Este projeto é de uso exclusivo interno e proprietário.
