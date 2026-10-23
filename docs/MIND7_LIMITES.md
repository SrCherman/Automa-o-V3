# Mind7 — coordenação de consultas e bloqueios

O portal informou bloqueio em 22/10/2026 às 19:08:22 (America/Sao_Paulo),
após 115 consultas em cinco minutos. Quando existir, o arquivo local
`data/mind7_access_blocked.txt` suspende o acesso entre reinícios.
O arquivo estava ausente na verificação local de 23/10/2026; isso **não prova**
que o provedor autorizou a retomada. Não remover um marcador criado pelo sistema
nem retomar consultas após recusa do portal sem confirmar a liberação.

## Proteções aplicadas

- Coleta por telefone, enriquecimento por CPF, rotas antigas e cliente HTTP
  compartilham `data/mind7_query_quota.sqlite` para serializar os envios.
  Não há teto numérico local por minuto: uma consulta aguarda a anterior terminar.
  Isso NÃO substitui os limites contratuais nem autoriza volume abusivo.
- Cada consulta aguarda o carregamento completo da resposta. Depois, a pausa
  segue o ciclo de 1, 2, 1 e 3 segundos, reiniciando nessa ordem. O ciclo e o
  horário da próxima consulta persistem entre etapas, processos e reinícios.
  Se o portal indicar bloqueio ou desafio, interromper e exigir revisão manual.
- A transação SQLite inclui o envio e a espera pela resposta. Processos e
  etapas concorrentes aguardam seu turno, sem sobrepor consultas Mind7.
  Falhas de envio também registram a tentativa e o intervalo posterior.
- Um envio interrompido por encerramento do processo deixa um arquivo
  `.inflight`. A próxima tentativa cria o bloqueio persistente e exige revisão
  manual, sem reenviar automaticamente.
- Cada consulta de navegador usa um único clique, sem ENTER adicional. O
  monitor de POSTs e o estado visível ajudam a diferenciar carregamento de
  um possível clique fantasma. Sem sinal de resposta até o timeout, a aba é
  atualizada, porém a consulta permanece pendente para revisão: não há reenvio
  automático incerto. O cliente HTTP não segue redirecionamentos nem tenta
  novamente pelo navegador.
- Avisos de bloqueio, desafios de validação e HTTP 403/429 suspendem novas
  consultas. Desafios exigem atenção manual; não há tentativa de contorno.
- Contatos interrompidos permanecem pendentes; bloqueios e cancelamentos não
  gastam as tentativas de processamento do contato.

Essas pausas são locais, não uma confirmação dos
limites contratuais do Mind7. A liberação e os limites permitidos precisam ser
confirmados pelo provedor. Processos em outra máquina ou consultas manuais não
participam dessa serialização local; não executar o mesmo acesso em paralelo fora dela.

## Validação offline

`python -m pytest` executa a suíte offline, com mocks nos envios e arquivos
temporários para o coordenador e o marcador. O teste manual que se conecta ao navegador
real é ignorado. Os testes não removem nem modificam o marcador de produção.

O estado do painel em `/api/automation/state` informa `mind7_access_blocked`.
Enquanto o marcador existir, iniciar campanhas retorna `status: blocked` e
nenhum worker é iniciado.
