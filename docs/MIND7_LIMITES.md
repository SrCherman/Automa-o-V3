# Consultas Mind7 suspensas

O portal informou bloqueio em 22/10/2026 às 19:08:22 (America/Sao_Paulo),
após 115 consultas em cinco minutos. O arquivo local
`data/mind7_access_blocked.txt` mantém a suspensão entre reinícios.
Não remover esse arquivo nem retomar consultas antes da liberação do provedor.

## Proteções aplicadas

- Coleta por telefone, enriquecimento por CPF, rotas antigas e cliente HTTP
  compartilham `data/mind7_query_quota.sqlite`: até 20 consultas em uma janela
  de 300 segundos, com pelo menos 15 segundos entre envios.
- A transação SQLite inclui o envio efetivo. Processos e etapas concorrentes
  aguardam a mesma quota. Falhas de envio também consomem quota.
- Um envio interrompido por encerramento do processo deixa um arquivo
  `.inflight`. A próxima tentativa cria o bloqueio persistente e exige revisão
  manual, sem reenviar automaticamente.
- Cada consulta de navegador usa um único clique, sem ENTER adicional.
  O cliente HTTP não segue redirecionamentos nem tenta novamente pelo navegador.
- Avisos de bloqueio, desafios de validação e HTTP 403/429 suspendem novas
  consultas. Desafios exigem atenção manual; não há tentativa de contorno.
- Contatos interrompidos permanecem pendentes; bloqueios e cancelamentos não
  gastam as tentativas de processamento do contato.

Esses valores são uma proteção local conservadora, não uma confirmação dos
limites contratuais do Mind7. A liberação e os limites permitidos precisam ser
confirmados pelo provedor. Processos em outra máquina ou consultas manuais não
participam desta quota local; não executar o mesmo acesso em paralelo fora dela.

## Validação offline

`python -m pytest` executa a suíte com conexões de rede bloqueadas e usa arquivos
temporários para a quota e o marcador. O teste manual que se conecta ao navegador
real é ignorado. Os testes não removem nem modificam o marcador de produção.

O estado do painel em `/api/automation/state` informa `mind7_access_blocked`.
Enquanto o marcador existir, iniciar campanhas retorna `status: blocked` e
nenhum worker é iniciado.
