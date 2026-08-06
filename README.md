# Busca de Preços — Pertin Market

Busca o preço de venda de cada produto da planilha de compras em supermercados
concorrentes, calcula o markup praticado e marca se o preço da loja está `OK`
ou precisa `AJUSTAR`.

## Instalação

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Como rodar

**Interface gráfica** (é o que o cliente vai usar):

```bash
.venv/bin/python app.py
```

Escolher planilha → marcar lojas → **Buscar preços**. Tem barra de progresso
por loja, botão Cancelar (salva o parcial) e "Abrir planilha" no fim. A busca
roda em thread separada, então a janela não congela.

**Linha de comando** (para você):

```bash
.venv/bin/python main.py
```

| Comando | O que faz |
| --- | --- |
| `main.py` | Passe 1: busca todas as lojas, matching restrito (`min_score=68`) |
| `main.py --pass2` | Passe 2: só preenche células **vazias**, matching flexível (`min_score=52`) |
| `main.py --somente-calculo` | Recalcula as colunas derivadas sem chamar API |
| `main.py --no-local` | Grava na própria planilha (cria `.bak-<data>` antes) |
| `main.py --saida ARQ` | Escolhe o arquivo de saída |
| `main.py --sem-cache` | Ignora o cache e consulta tudo de novo |

Por padrão **não sobrescreve a entrada**: grava
`<nome>_precos_<data-hora>.xlsx` ao lado. `Ctrl+C` interrompe e salva o que já
foi encontrado.

## Estrutura

```
app.py                  ponto de entrada do .exe — resolve payload + checa atualização
main.py                 casca do CLI — fina de propósito
build.py                empacota (exe + payload + version.json)
BuscaPrecos.spec        receita do PyInstaller
MANUAL-CLIENTE.md       manual que vai junto com o programa
config.json             CEP, lojas, markups, limiares, arquivos
categorias.csv          247 palavras-chave → categoria de markup (editável)
buscaprecos/            o "payload": toda a lógica, substituível por OTA
├── texto.py            normalização, score e barreiras de compatibilidade
├── precos.py           conversão de preço/percentual/EAN
├── lojas.py            clientes VipCommerce, VTEX e estoque local
├── regras.py           markup por categoria e colunas derivadas
├── planilha.py         leitura/escrita XLSX e CSV
├── busca.py            orquestração (paralelo por loja, cancelável)
├── cache.py            cache SQLite com validade
├── rede.py             sessão HTTP com retry e disjuntor
├── config.py           carga do config.json
├── gui.py              interface Tkinter (busca em thread, fila para a UI)
└── atualizacao.py      checagem e instalação da atualização OTA
tests/                  94 testes (casamento, regras, parser, config, colunas)
historico/              planilhas, logs e os scripts da v1 (referência)
```

## Lojas

| Coluna | Fonte | Busca por EAN |
| --- | --- | --- |
| VERDEMAR | VipCommerce | sim (campo `codigo_barras`) |
| VILLEFORTE | VipCommerce | sim |
| SUPERNOSSO | VTEX catálogo | não indexa EAN — cai no texto |
| LOJAS AMERICANAS | VTEX catálogo | sim (`fq=alternateIds_Ean`) |
| ATACADAO | VTEX catálogo | sim (`fq=alternateIds_Ean`) |
| CARREFOUR | VTEX Intelligent Search | sim (`query=<ean>`) |
| ARAUJO | Salesforce Commerce (HTML) | sim (EAN sai da URL da imagem) |
| PAULO | `Relatorio_planograma…` | EAN → código → ID → nome |

**Loja listada é loja preenchida.** Não existe tipo "coluna que o cliente
digita": criar coluna na planilha ele faz sozinho, e ver o supermercado na
lista faz esperar que o preço venha. Mart Minas e Epa chegaram a entrar assim
na v1.1.1 e foram retiradas na v1.2.0.

### As duas gerações de VTEX

Lojas VTEX expõem uma de duas APIs, e o cliente tenta as duas em ordem:

- **catálogo** (`/api/catalog_system/pub/products/search`) — Supernosso,
  Americanas, Atacadão.
- **Intelligent Search** (`/api/io/_v/api/intelligent-search/…`) — Carrefour,
  onde o catálogo antigo devolve **403**.

Na Intelligent Search o termo vai em **`query=` como parâmetro**. Passado como
segmento de caminho ela responde 200 e ignora a busca, devolvendo o catálogo
inteiro (`recordsFiltered` de 21 milhões) — parece resultado válido e não é.

### Lojas sem preço na web

Mart Minas e Epa foram investigados e **não entram no cadastro**: o site é
institucional (WordPress e páginas regionais), não há API de produto em nenhum
subdomínio, não são VipCommerce, e as ofertas saem em **encarte PDF/imagem e
app**.

Automatizá-las exigiria OCR de encarte (só pega item de promoção) ou engenharia
reversa do app. Nenhum dos dois se paga — e listá-las sem buscar é pior que não
listar.

## Como o casamento funciona

O risco do sistema não é falhar, é **acertar errado**: gravar um preço
plausível de outro produto. Por isso o preço só é aceito depois de passar por
barreiras independentes do score.

1. **Match exato por código de barras** — quando a loja indexa EAN. É o
   caminho preferido e não depende de texto nenhum.
2. **Match por texto** — `score_match` pontua, e o candidato ainda tem que
   passar por: marca compatível, substantivo principal presente e
   **gramatura compatível**.
3. **Fonte permissiva** (estoque por nome, Araújo) — passa por todas as
   barreiras, incluindo variante (ZERO/DIET) e sabor.

O passe por texto também barra **embalagem múltipla**: uma consulta de unidade
não casa com fardo nem kit. Sem isso, "REFRIG COCA COLA LT 350ML" pegava
R$ 23,34 no Supernosso (fardo de 12) — a trava de gramatura não ajuda porque o
fardo declara a mesma gramatura unitária.

Variante e sabor **não** entram no passe por texto de propósito: a nota vem
abreviada (`LEITE LV ITAMBE TP 1L INTEG`) e exigir a palavra inteira
rejeitaria o produto certo.

Mexeu em limiar? Rode os testes:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Colunas que o programa escreve

As de loja (`VERDEMAR`…`ARAUJO`) e: `MENOR PREÇO`, `SUPERMERCADO MENOR`,
`MAIOR PREÇO`, `SUPERMERCADO MAIOR`, `MÉDIA 3 MAIORES`, `MARKUP ALVO`,
`PREÇO PERTIN`, `MARGEM`, `REGRA`, `QTD PREÇOS`, `OBS BUSCA`.

Nenhuma outra coluna é tocada. Célula que já contém fórmula é **preservada** —
o cálculo do Excel tem prioridade, e o programa avisa quantas encontrou.

`QTD PREÇOS` e `OBS BUSCA` são novas e existem para tornar visível o que antes
era invisível. `OBS BUSCA` avisa quando:

- a média saiu de menos de 3 preços;
- falta custo unitário ou markup alvo;
- **o preço de uma loja destoa mais de 3× da mediana das outras** — é o caso
  que nenhuma barreira de texto pega, porque quando a descrição não declara
  gramatura ("DOCE PAÇOCA PAÇOQUITA") nada impede o site devolver o preço da
  caixa fechada. Na planilha de julho isso aconteceu em 43 linhas.
- **o nome casado por EAN declara embalagem múltipla** ("pode ser embalagem de
  2"). Carrefour cadastra "Kit 2 Biscoito Oreo" sob o código de barras da
  unidade. Rejeitar seria pior: Supernosso devolve "Caixa com 12" pelo mesmo
  caminho com preço unitário correto — então o programa sinaliza em vez de
  descartar.

## Planilha de entrada

**Não precisa de formato exato.** O programa procura o cabeçalho nas 10
primeiras linhas e reconhece as colunas por apelido — `Descrição do Produto`,
`Descrição`, `PRODUTOS`, `Item`, `Produto` são todas aceitas como descrição; o
mesmo para EAN, código interno, custo e markup. As colunas de loja e as
calculadas são **criadas** se não existirem.

Só a **descrição** é indispensável. As outras são graduais:

| Sem esta coluna | O que se perde |
| --- | --- |
| EAN | a busca cai para texto, com mais erro |
| Código interno | o preço da própria loja acha menos |
| Custo unitário | não dá para calcular MARGEM, PREÇO PERTIN nem REGRA |
| Markup | ele é deduzido da categoria pelas palavras da descrição |

Quando a detecção não reconhece a descrição, a interface abre a tela
*Colunas…* para o cliente apontar qual é qual — e guarda a escolha. Ou seja:
planilha em formato inesperado não exige atualização do programa.

## Cadastro de lojas

O cliente pode incluir lojas **VipCommerce** e **VTEX** pela tela
*Cadastrar lojas…*: nome, plataforma e endereço, sem uma linha de código. Uma
plataforma nova (site próprio, Shopify, Magento) precisa de um cliente novo em
`lojas.py` — isso é atualização.

**O cadastro testa antes de aceitar.** Endereço ou plataforma errados não dão
erro: a coluna simplesmente fica vazia. Antes disso só se descobria depois de
esperar a busca inteira — meia hora para saber que o cadastro estava errado.
Agora a consulta é feita na hora e a loja só entra se responder; se falhar, o
programa explica e pergunta se deve cadastrar mesmo assim.

Durante a busca há uma segunda rede: uma loja que não achou nada nas primeiras
20 consultas é denunciada na hora ("confira o endereço e a plataforma"), e o
resumo final nomeia toda loja que terminou sem nenhum preço. Serve também para
o caso silencioso, em que o site responde 200 e nunca casa nada — aí o
disjuntor não abre e a coluna chegaria vazia sem explicação.

## Configuração

Tudo em [config.json](config.json), que na primeira abertura é copiado de
`config.padrao.json`. Os dois ajustes mais prováveis:

- **`estatisticas.lojas`** — quais lojas entram em `MENOR/MAIOR PREÇO` e
  `MÉDIA 3 MAIORES`. Hoje inclui `PAULO`, a própria loja. A planilha do
  cliente tem uma coluna `MAIOR PREÇO (SEM PAULO)` feita por fórmula, o que
  sugere que a referência de mercado **não** deveria incluir a própria loja.
  Tirar `PAULO` daqui muda a `REGRA` de todas as linhas — decisão do cliente.
- **`estatisticas.minimo_precos_para_regra`** — com 1 (padrão), a `REGRA` sai
  mesmo com um único preço de referência. Subir para 3 deixa `REGRA` vazia
  nessas linhas e registra o motivo em `OBS BUSCA`.

## Araújo

O Araújo não tem API JSON: o endpoint `Search-UpdateGrid` devolve HTML e fica
atrás de um WAF. Antes isso exigia colar JavaScript no console de uma aba
aberta — era o que impedia o cliente rodar sozinho. Agora é um cliente HTTP
comum, com três detalhes que fazem funcionar:

1. **Cabeçalhos de navegador completos.** Só `User-Agent` toma 403; precisa
   também de `Accept`, `Accept-Language`, `Referer` e `X-Requested-With`.
2. **Sessão reutilizada e ritmo próprio** (`pausa_araujo_seg`, padrão 1,2s).
   Rajada sem sessão toma 403; com sessão e ~1s ele responde 200 consistente.
3. **403 é limite de taxa, não "produto inexistente".** Tratá-lo como
   resposta vazia gravaria "não encontrado" em massa, então ele entra em
   espera progressiva e tenta de novo.

### O bloqueio depende da rede, não do ritmo

Medido em duas máquinas, com cabeçalhos e ritmo idênticos:

| Transporte | Linux (dev) | Windows (cliente) |
| --- | --- | --- |
| `padrao` (requests comum) | 200 | **403** |
| `tls_navegador` (TLS ajustado) | 200 | **200** |

Ritmo não muda nada: 1,2s e 5s dão o mesmo resultado nas duas. O que o WAF
recusa é a **impressão digital do handshake TLS** — ele vê um "Chrome 120"
cujo TLS é de Python/OpenSSL. `tls_navegador` ajusta a ordem de cifras que o
Python deixa configurar, e isso bastou.

Por isso `tls_navegador` é o **primeiro** transporte da fila: é o único medido
funcionando nas duas redes. `padrao` fica atrás como reserva (se o OpenSSL
local recusar a lista de cifras), e depois dele vêm as opções que exigem
dependência binária:

| Transporte | Chega por atualização de payload? |
| --- | --- |
| `tls_navegador` | **sim** |
| `padrao` | sim |
| `curl_cffi` | não — regerar o `.exe` |
| `navegador` (Playwright + Edge) | não — regerar o `.exe` |

O programa troca sozinho quando toma 403 repetido, então nenhuma configuração
é necessária. Para fixar um transporte, use `busca.araujo_transporte` no
config. Para as duas últimas opções:

```bash
pip install curl_cffi
```

O botão **Diagnóstico** testa os quatro e diz qual usar.

## Atualização OTA

O executável (interpretador + dependências) quase nunca muda. O que muda é o
payload — a pasta `buscaprecos/` em `.py`, carregada em runtime. Atualizar é
trocar a pasta e reiniciar: nenhum arquivo em uso é sobrescrito e não precisa
de administrador.

Publicar uma versão:

```bash
python build.py 1.1.0 --sem-exe --notas "corrige match do Atacadão"
```

```bash
gh release create v1.1.0 dist/payload-1.1.0.zip dist/version.json --notes "o que mudou"
```

A base da URL fica lembrada em `.build.json` depois da primeira vez — sem isso
é fácil gerar um `version.json` apontando para o lugar errado e a atualização
falhar em silêncio.

Publicado em <https://github.com/Sikansi/busca-precos/releases>. O app consulta
`releases/latest/download/version.json`, que sempre resolve para o release mais
recente.

### O que uma atualização pode mudar no config do cliente

O `config.json` do cliente é preservado — certo para as preferências dele,
errado para o que eu mantenho. A divisão:

| | Quem manda |
| --- | --- |
| CEP, colunas, planilha, liga/desliga de loja | **cliente** |
| URL de atualização (só se estiver em branco) | eu |
| `tipo` e `endereco` das lojas que vêm no seed | eu |
| lojas que o cliente cadastrou | **cliente**, intocadas |
| remover loja | ninguém: atualização nunca remove |

Isso existe porque publicar o Carrefour no seed **não teve efeito nenhum** em
quem já usava o programa: o seed só é lido quando não há `config.json`. Loja
nova agora é mesclada, e loja do seed cadastrada com plataforma errada é
corrigida — senão ela fica vazia para sempre e parece que o supermercado não
tem os produtos. Toda mudança aparece na área de Detalhes ao abrir. Sem isso, uma
instalação antiga com o campo vazio nunca receberia correção — e a pasta
enviada ao cliente **não leva `config.json`** (ele tem os caminhos das
planilhas da máquina de desenvolvimento), então o seed é o único lugar onde
essa URL pode estar.

### Duas armadilhas do GitHub Releases

Descobertas publicando de verdade, as duas silenciosas:

1. **`releases/latest/download/` é servido com cache.** Medi `Age: 769` —
   quase 13 minutos devolvendo a versão anterior depois de publicar a nova. A
   checagem manda `Cache-Control: no-cache`; sem isso o cliente demora, ou
   deixa, de ver a atualização.
2. **`releases/latest/download/payload-1.0.4.zip` vira 404** no instante em
   que a 1.0.5 sai, porque o asset só existe no release dele. Combinado com o
   cache acima, um `version.json` velho manda o cliente buscar arquivo que não
   existe mais. Por isso `url_zip` é **fixada na tag**
   (`releases/download/v1.0.6/…`), que vale para sempre — é o que
   `url_do_asset()` garante.

Se um `version.json` já foi publicado com URL em `latest`, dá para consertar
sem apagar release: anexe o payload antigo também ao release novo, e o caminho
volta a resolver.

```bash
gh release upload v1.0.6 payload-1.0.5.zip
``` O cliente abre o app, ele consulta o `version.json`,
mostra "Nova versão 1.1.0 disponível", e ao clicar baixa, confere o SHA-256,
extrai e reinicia. A versão anterior fica no disco — é o rollback.

Sem certificado de assinatura, o Windows mostra "aplicativo não reconhecido"
na primeira execução: **Mais informações → Executar assim mesmo**. Só na
primeira vez — está no [MANUAL-CLIENTE.md](MANUAL-CLIENTE.md).

### Por que o payload é separado do executável

O Windows tranca o `.exe` em execução, então ele não consegue sobrescrever a
si mesmo. Separando em duas camadas o problema desaparece:

```
BuscaPrecos/
├── BuscaPrecos.exe        interpretador + dependências — muda raramente
├── _internal/
├── config.json            criado na 1ª abertura; NUNCA é tocado por update
└── payload/
    ├── ATUAL              texto: "1.0.0"
    ├── 1.0.0/             versão anterior (rollback)
    └── 1.1.0/             buscaprecos/, categorias.csv, config.padrao.json
```

Atualizar é extrair uma pasta nova e reescrever `ATUAL`. Nenhum arquivo em uso
é sobrescrito e não precisa de administrador. O `config.json` fica **fora** do
payload de propósito: dentro, cada atualização apagaria CEP, lojas e colunas
do cliente.

## Do Linux até o cliente

São **dois zips diferentes**, e confundi-los é o erro fácil:

| Zip | O que tem | Vai para |
| --- | --- | --- |
| projeto | código-fonte (esta pasta) | sua máquina Windows, para gerar o .exe |
| `dist/BuscaPrecos` | .exe + `_internal/` + `payload/` + manual | o cliente |

**Caminho A — sem instalar nada no Windows (recomendado).** Suba o projeto no
GitHub e rode o workflow *Build Windows* em Actions (ou crie uma tag). Ele
builda em `windows-latest`, roda os testes antes e devolve o zip pronto como
artifact. Zero setup.

**Caminho B — na sua máquina Windows.** Zipe esta pasta, descompacte no
Windows e dê **dois cliques em `GERAR-EXE.bat`**. Ele cria o venv, instala as
dependências, roda os testes, empacota e abre a pasta do resultado. Precisa de
**Python 3.10+ instalado com "Add python.exe to PATH"** marcado — o `.bat`
avisa e para se não achar.

**O cliente não faz nenhum dos dois.** Ele recebe o zip de
`dist/BuscaPrecos`, descompacta onde quiser e dá dois cliques em
`BuscaPrecos.exe`. Não instala nada, não precisa de Python nem de
administrador — a pasta é portátil. Não existe instalador porque não é
necessário; se um dia quiser atalho no Menu Iniciar, aí sim entra um Inno
Setup.

> Mande a **pasta inteira**, não só o `.exe`. Sozinho ele não roda: precisa de
> `_internal/` (bibliotecas) e `payload/` (a lógica).

## Gerar o executável na mão

```bash
python build.py 1.0.0
```

Precisa rodar **no Windows** — o PyInstaller não faz cross-compile. Do Linux:

```bash
python build.py 1.1.0 --sem-exe --notas "o que mudou"
```

que gera só o payload e o `version.json` — suficiente para publicar uma
correção de lógica, que é o caso comum. Para o `.exe` sem máquina Windows, o
workflow [.github/workflows/build-windows.yml](.github/workflows/build-windows.yml)
faz o build no GitHub Actions (roda os testes antes) e, em tag, já publica o
Release com o `version.json`.

Escolhas do empacotamento: `--onedir` (o `--onefile` descompacta a cada
abertura e dispara mais antivírus), `console=False` (senão abre um prompt
preto atrás da janela) e UPX desligado (aumenta falso-positivo).

## Cuidados conhecidos

- **Supernosso não indexa EAN** em `alternateIds_Ean`, então depende do
  matching por texto e é a loja com mais chance de falso positivo.
- **O Araújo é raspagem de HTML** e vai quebrar quando o site mudar de
  layout. É a parte mais frágil. O parser tem teste com HTML de amostra
  (`tests/dados/araujo_grade.html`): quando quebrar, atualize a amostra e o
  teste mostra exatamente o que mudou.
- **`MÉDIA 3 MAIORES` com menos de 3 preços** é a média do que existir — veja
  `QTD PREÇOS` antes de confiar na `REGRA` da linha.
- **CEP fixo** (`config.json`). Preço de VTEX pode variar por região.
- **`REGRA` está saturada**: 397 de 403 linhas dão `AJUSTAR` porque a
  tolerância é de 0,5pp (`estatisticas.tolerancia_margem`). Uma faixa
  realista (2 a 5pp) tornaria a coluna informativa. Decisão do cliente.
- **Margens de centenas de por cento** aparecem quando o custo da nota está
  em unidade diferente do preço de varejo (custo por unidade dentro de uma
  caixa de 21 × preço do pacote na prateleira). É problema do dado de
  entrada, não do cálculo — vale conferir com o cliente.
- O xlsx que circulava **já tinha perdido as fórmulas** num ida-e-volta por
  CSV anterior; as originais só existem em
  `Compras_Consolidadas_categorizado_2507 1020.xltx`. **Rodar sobre o template
  é a melhor opção**: as fórmulas voltam (medido: 4.445 preservadas) e a saída
  sai como `.xlsx` normal.
- Template salvo com nome `.xlsx` mas declarando `template` por dentro faz o
  Excel recusar o arquivo ("o formato ou a extensão não é válida"), enquanto o
  Google Sheets abre — o que faz o defeito parecer problema do Excel. Por isso
  `_salvar_xlsx` ajusta `Workbook.template` conforme a extensão de saída.
