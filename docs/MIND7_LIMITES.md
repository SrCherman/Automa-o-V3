# Mind7 — coordenação de consultas e bloqueios

O portal informou bloqueio em 22/10/2026 às 19:08:22 (America/Sao_Paulo),
após 115 consultas em cinco minutos. Bloqueio declarado pelo portal continua
suspenso entre reinícios por `data/mind7_access_blocked.txt`.

## Retomada automática

- Ao iniciar uma etapa, o worker faz um GET inofensivo (`probe_mind7_access`)
  com o perfil autenticado. Se o formulário abrir sem desafio, o marcador é
  removido e o trabalho continua; se não abrir, o marcador permanece.
- Desafio Cloudflare não é mais tratado como bloqueio definitivo: a página é
  observada por até 25 s. Resolvido o desafio, a consulta segue; persistindo,
  a etapa tenta de novo com espera crescente (4 s a 64 s, 5 tentativas) sem
  gastar tentativa do contato.
- Envio interrompido há mais de 10 min (`.inflight` órfão) é descartado; um
  `.inflight` recente ainda exige revisão.
- Desafio no POST (403 com `cf-mitigated`) é detectado pelo monitor de rede:
  a página é recarregada, o checkbox do Turnstile é clicado e o envio repetido
  (até 3 vezes, dentro do mesmo turno da fila).
- Se o portal derrubar a sessão, `_logar_se_preciso` entra de novo com o login
  do `.env` e aguarda o Turnstile antes de enviar o formulário.
- O resultado pode vir sem `#copiar_dados` (layout novo): nesse caso vale o
  texto visível do painel, exigindo 1 s de estabilidade antes de aceitar.

## Proteções aplicadas

- Coleta por telefone, enriquecimento por CPF, rotas antigas e cliente HTTP
  compartilham `data/mind7_query_quota.sqlite` para serializar os envios.
  Não há teto numérico local por minuto: uma consulta aguarda a anterior terminar.
- Pausa após cada resposta: ciclo 2,2 / 3,0 / 2,4 / 3,6 s com variação de
  -0,25 a +0,35 s (≈ 21 consultas/min) e pausa de 12–22 s a cada 50 envios.
  O ciclo e o horário da próxima consulta persistem entre etapas e reinícios.
- Cada consulta aguarda o carregamento completo da resposta antes da pausa.
  O navegador usa perfil persistente, `cf_clearance` do próprio navegador e
  `--disable-blink-features=AutomationControlled`; o cliente HTTP reutiliza o
  mesmo User-Agent do navegador, nunca um UA próprio.
- A transação SQLite inclui o envio e a espera pela resposta. Processos e
  etapas concorrentes aguardam seu turno, sem sobrepor consultas Mind7.
  Falhas de envio também registram a tentativa e o intervalo posterior.
- Um envio interrompido por encerramento do processo deixa um arquivo
  `.inflight` recente que exige revisão, sem reenviar automaticamente.
- Cada consulta de navegador usa um único clique, sem ENTER adicional. O
  monitor de POSTs e o estado visível ajudam a diferenciar carregamento de
  um possível clique fantasma. Sem sinal de resposta até o timeout, a aba é
  atualizada, porém a consulta permanece pendente para revisão: não há reenvio
  automático incerto. O cliente HTTP não segue redirecionamentos nem tenta
  novamente pelo navegador.
- Avisos de bloqueio declarado e HTTP 403/429 suspendem novas consultas.
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
Iniciar com marcador ainda é permitido: o worker confirma o portal antes da
primeira consulta e mantém a suspensão se ele não liberar.
