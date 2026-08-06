# Busca de Preços — como usar

Este programa procura o preço dos seus produtos nos supermercados
concorrentes e preenche a planilha, calculando o markup e apontando o que
precisa de ajuste.

---

## 1. Primeira vez que abrir

Descompacte a pasta `BuscaPrecos` onde quiser (Documentos, Desktop, tanto
faz) e dê dois cliques em **BuscaPrecos.exe**.

### O Windows vai mostrar um aviso — é esperado

> **O Windows protegeu o seu PC**
> O Microsoft Defender SmartScreen impediu a inicialização de um aplicativo
> não reconhecido.

Isso **não** significa vírus. Significa que o programa não tem certificado de
assinatura digital — é um documento pago que só faz sentido para software
vendido em larga escala. Para abrir:

1. Clique em **Mais informações**
2. Clique em **Executar assim mesmo**

Só é preciso fazer isso **uma vez**. Nas próximas o Windows abre direto.

> Se o antivírus reclamar, libere a pasta inteira nas exceções. Programas
> feitos em Python empacotado disparam alerta falso com alguma frequência.

---

## 2. Rodando uma busca

1. **Planilha** — clique em *Escolher…* e selecione sua planilha de compras
   (`.xlsx` ou `.csv`).
2. Confira as colunas que o programa reconheceu. Se ele avisar que não
   reconheceu alguma, clique em *Colunas…* e aponte qual é qual.
3. **Onde buscar** — desmarque as lojas que não interessam nesta rodada.
4. Clique em **Buscar preços**.

A busca leva de 20 a 60 minutos para uma planilha de ~400 produtos. Cada loja
tem sua própria barra de progresso. Pode usar o computador normalmente.

**A sua planilha original nunca é alterada.** O resultado sai em um arquivo
novo, com data e hora no nome, na mesma pasta. Ao terminar, o botão *Abrir
planilha* abre o resultado.

Precisa parar no meio? **Cancelar** salva tudo o que já foi encontrado.

---

## 3. Entendendo o resultado

Além das colunas de cada supermercado, o programa preenche:

| Coluna | O que é |
| --- | --- |
| MENOR PREÇO / MAIOR PREÇO | O menor e o maior encontrados, e em qual loja |
| MÉDIA 3 MAIORES | Média dos três preços mais altos |
| MARKUP ALVO | O markup da categoria do produto |
| PREÇO PERTIN | Custo + markup alvo |
| MARGEM | Margem que os concorrentes estão praticando |
| REGRA | `OK` ou `AJUSTAR` |
| **QTD PREÇOS** | Quantas lojas responderam |
| **OBS BUSCA** | Avisos — **leia esta coluna** |

### Confira sempre a coluna OBS BUSCA

Ela avisa quando o número não é confiável:

- **"conferir preço de ATACADAO"** — o preço daquela loja está muito fora dos
  outros. Normalmente é o programa tendo pegado o preço da **caixa fechada**
  em vez da unidade. Vale conferir no site antes de usar.
- **"média sobre 2 preço(s)"** — poucas lojas responderam, então a média
  representa pouco.
- **"nenhum preço encontrado"** — nenhuma loja tinha o produto.
- **"sem custo unitário"** — falta o custo na planilha, então não deu para
  calcular margem.

Célula de loja vazia significa "não encontrei", não "não existe".

---

## 4. Ajustando o programa

### CEP

Alguns sites mostram preço diferente por região. O campo **CEP** na tela
principal define a região consultada.

### Incluir ou tirar supermercados

Em **Cadastrar lojas…** você pode incluir supermercados novos. Precisa de:

- **Nome** — vira o nome da coluna na planilha
- **Plataforma** — `vtex` ou `vip`
- **Endereço** — a URL do site (VTEX) ou o domínio da loja (VipCommerce)

Ao clicar em **Incluir**, o programa faz uma consulta de teste na hora e só
cadastra se a loja responder. Se não responder, ele explica o motivo e pergunta
se você quer cadastrar mesmo assim.

Isso existe porque endereço ou plataforma errados **não dão erro**: a coluna
simplesmente fica vazia, e parece que o supermercado não tem os produtos.

Se o teste falhar, me chame — pode ser uma plataforma que o programa ainda não
sabe ler, e isso eu acrescento por atualização.

> Toda loja da lista é preenchida pelo programa. Se algum supermercado que
> você quer não aparece e não passa no teste, é porque ele não publica preço
> na internet (alguns só divulgam encarte em PDF ou no aplicativo). Nesse caso
> vale criar a coluna direto na sua planilha e preencher à mão — o programa
> não mexe nas colunas que não são dele.

Suas alterações ficam salvas e valem nas próximas aberturas.

---

## 5. Atualizações

Quando eu corrigir ou melhorar algo, o programa avisa sozinho ao abrir:

> **Atualização disponível** — Versão 1.1.0. Atualizar agora?

Clique em **Sim**. Ele baixa, instala e pede para você abrir de novo. Leva uns
segundos e **não apaga nenhuma configuração sua** — CEP, lojas cadastradas e
mapeamento de colunas continuam.

Sem internet ou com o servidor fora, o programa abre normalmente na versão
atual.

---

## 6. Quando algo der errado

Os sites dos supermercados mudam sem avisar, e quando mudam o programa para de
achar preço naquela loja. Sinais:

- Aparece **"não achou nenhum preço nos primeiros 20 produtos"** logo no
  começo da busca
- No fim, o resumo diz **"sem nenhum preço: NOME DA LOJA"**
- Aparece **"ficou fora do ar"** na área de Detalhes
- Preços claramente errados numa loja específica

Nesses casos: copie o que está na área **Detalhes** e me mande, junto com o
nome da loja. Com isso eu identifico o que mudou e publico uma atualização —
você só clica em *Sim* na próxima abertura.

O programa foi feito para não parar por causa de uma loja: se uma sai do ar,
as outras continuam e o resultado sai com o que deu.
