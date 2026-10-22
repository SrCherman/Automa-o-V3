# Fluxo de consultas C6

1. Telefone no Mind7: confirmar nome e CPF.
2. CPF no Mind7: guardar nascimento completo, CEP e número. Todos os CPFs entram, independentemente do crédito no Itaú. Idade ou ano de nascimento nunca substituem a data exata. A coluna antiga de idade fica apenas como histórico; a interface e a exportação usam **Data de Nascimento**.
3. Itaú: consulta própria, sem sobrescrever o C6.
4. C6: depende do nascimento completo; consulta própria, sem depender de aprovação no Itaú.

No painel, telefone e CPF usam abas fixas diferentes do mesmo navegador Mind7. As etapas consomem os contatos assim que os dados anteriores ficam gravados. Os navegadores dos bancos são separados por campanha.

## Dados fixos confirmados

- Placa: **SEO7H39**.
- Valor do veículo: **R$ 130.000,00**.
- Entrada: **em branco**; a automação não informa R$ 13.000,00.
- UF de licenciamento: **PR**.
- Cliente: **com CNH**.

Esses dados do C6 ficam em `src/config.py`. Os campos de placa e valor no modal de início continuam configurando o Itaú. O C6 encerra a coleta ao encontrar o valor sugerido de **Financiamento**, sem avançar para contratação. A mensagem “Fora da Política” é um resultado do C6, sem valor financiado; não significa automaticamente CPF inválido.

O formulário atual também exige **Tipo de veículo**. Para esta consulta, a automação seleciona **VEÍCULOS LEVES** e confirma a escolha antes de preencher a placa.

Valores exibidos junto de “Simulando”, “erro” ou “Selecione um valor permitido” são provisórios e não são gravados como aprovação. Se a tela não chegar a um resultado reconhecido após o envio, a consulta fica para conferência manual.

## Base existente e retomada

Abra `Iniciar_Produto.bat` e escolha **Etapa 4 - C6 Auto** ao iniciar a campanha. Essa opção complementa os dados por CPF e percorre o C6, preservando o Itaú. **Etapa 2 - Enriquecimento por CPF** também pode ser executada sozinha.

A atualização acrescenta as colunas à base existente, sem apagar contatos ou resultados. Antes da primeira alteração de uma base antiga, cria `data/database.before_c6.sqlite`. Os campos novos incluem nascimento, estado do enriquecimento, tentativas, observação e data; o C6 recebe status, valor financiado, observação, tentativas e data próprios.

Bases antigas guardavam apenas a idade. Nesses casos é necessária uma consulta por CPF para obter o nascimento completo, preservando os dados cadastrais já válidos. Contatos sem CPF não podem ser consultados no C6 e ficam identificados como **AGUARDANDO CPF**.

Consultas concluídas não são repetidas ao reiniciar. Falhas transitórias permitem até três tentativas. Login ou verificação pendente do navegador pausam a etapa e preservam a fila. A aplicação aguarda a validação normal do Cloudflare; não contorna desafios.

Se o envio ao C6 já ocorreu e a resposta se perdeu, o status fica **CONFERIR RESULTADO**. Depois de conferir no portal, é possível usar “Reabrir consulta C6” na edição do contato. O estado evita reenvios automáticos em uma retomada. O modo de execução suportado é um único servidor local; não execute duas instâncias da aplicação sobre a mesma base.

O retorno para uma nova consulta abre o painel oficial, aciona **CRIAR UMA NOVA PROPOSTA** e confirma que o CPF está vazio antes de preencher outro contato.

## Acesso e planilha

O login usa `C6_USER` e `C6_PASS` do `.env` local, que é ignorado pelo controle de versão. As credenciais não devem ser colocadas no código nem nos relatórios.

O exportador do painel inclui nascimento, estado do enriquecimento e os campos do C6. O inicializador de planilha também oferece enriquecimento e C6 separados; as novas colunas são adicionadas pelo título, preservando as colunas antigas. Os atalhos antigos `--etapa2` (Itaú) e `--etapa3` (enriquecimento) continuam aceitos; prefira `--credline`, `--enriquecimento` e `--c6`.

## Validação

Os testes locais cobrem extração do nascimento, CPF divergente, preservação da base antiga, tentativas limitadas, resultados independentes, entrada em branco e retomada após envio incerto. A execução bancária real exige a validação final no portal; o layout e as respostas do C6 podem mudar.
