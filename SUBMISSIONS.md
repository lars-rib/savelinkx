# Pacote de submissão — SaveLinkX

Dados prontos para copiar e colar. Arquivo interno, não é documentação do produto.

---

## 1. awesome-selfhosted

**Status: bloqueado até ~dezembro/2026.** A regra é explícita: o projeto precisa ter
sido *lançado* há mais de 4 meses, e "lançado" significa release com tag. O repo não
tinha nenhuma tag — o relógio só começou com a `v1.0.0` de 12/08/2026. Antes disso a
submissão é rejeitada de cara.

Outros requisitos, todos já atendidos: licença open source (MIT), documentação em
inglês (README), self-hostável sem depender de nuvem específica, e não é PaaS.

Quando chegar a hora, é um pull request em `awesome-selfhosted/awesome-selfhosted-data`
criando `software/savelinkx.yml`:

```yaml
name: SaveLinkX
website_url: https://www.savelinkx.com
source_code_url: https://github.com/lars-rib/savelinkx
description: Download public videos from social platforms as MP4 or extract audio, with a trilingual web UI.
licenses:
  - MIT
platforms:
  - Python
  - Docker
tags:
  - Media Streaming - Video
depends_3rdparty: false
```

> Confirme os valores de `tags` e `platforms` contra os arquivos `tags/*.yml` e
> `platforms/*.yml` do repositório na hora de abrir o PR — a lista é fechada e muda.

---

## 2. AlternativeTo

**Precisa de conta.** Concorrentes diretos já estão listados, então a categoria existe.

| Campo | Valor |
| --- | --- |
| Name | SaveLinkX |
| URL | https://www.savelinkx.com |
| Description (curta) | Free web tool to download public videos from X, TikTok, Instagram, Facebook, Reddit, Vimeo, Dailymotion and Pinterest as MP4, or extract the audio as MP3. |
| License | Open Source (MIT) |
| Platforms | Web, Self-Hosted |
| Categories | Video Download, Online Services |
| Tags | video-downloader, video-download, self-hosted, open-source, mp3-converter |
| Alternative to | SaveLink.info, SaveTheVideo, 4K Video Downloader |

**Descrição longa:**

> SaveLinkX is a free, no-account web tool for downloading public videos from social
> platforms. Paste a link and pick a quality — the file downloads directly as MP4, or
> as MP3/M4A if you only want the audio. It also handles playlists (individually or as
> a ZIP), subtitles as .srt, and thumbnails.
>
> The interface is available in English, Portuguese and Spanish. There is no account,
> no subscription and no watermark.
>
> The whole thing is open source under MIT and self-hostable with Docker, so you can
> run your own instance instead of using the public one.

---

## 3. Product Hunt

**Precisa de conta.** É um tiro único — o dia do lançamento define o resultado, então
não faça às pressas.

| Campo | Valor |
| --- | --- |
| Name | SaveLinkX |
| Tagline (60 car. máx.) | Download videos from 9 platforms. Free, no account. |
| Links | https://www.savelinkx.com · https://github.com/lars-rib/savelinkx |
| Topics | Open Source, Developer Tools, Video, Self-Hosted |

**Description:**

> SaveLinkX downloads public videos from X, TikTok, Instagram, Facebook, Reddit,
> Vimeo, Dailymotion and Pinterest. Paste a link, pick a quality, get an MP4 — or pull
> just the audio as MP3.
>
> No account, no subscription, no watermark. Works in English, Portuguese and Spanish.
>
> It's MIT-licensed and runs anywhere with `docker compose up`, so you can self-host
> your own instance.

**Primeiro comentário (o maker comenta no próprio post — isso é esperado lá):**

> Hi PH 👋
>
> I built SaveLinkX because every video downloader I tried was buried in ads, popups or
> fake download buttons. I wanted the boring version: paste a link, get the file.
>
> It's a small Flask app on top of yt-dlp. No database, no accounts, no tracking beyond
> an optional privacy-friendly analytics beacon. The whole thing is MIT-licensed and
> self-hostable with one command, so if you don't trust my server you can run your own.
>
> Happy to answer anything about how it works.

---

## 4. Outros diretórios (mesmo conjunto de dados)

- **SaaSHub** — aceita open source, campos idênticos aos do AlternativeTo
- **Slant** — formato "melhor ferramenta para X", entra como opção numa pergunta existente
- **Awesome lists de nicho** — buscar por `awesome video tools`, `awesome downloaders`

---

## Notas honestas

- Links do GitHub e da maioria desses diretórios são `nofollow`. O ganho é descoberta,
  tráfego de referência e elegibilidade — não autoridade direta.
- **Nenhum desses pode ser enviado por mim.** Todos exigem criar conta, e criar conta
  ou digitar senha é coisa que eu não faço. Se você já estiver logado, eu preencho os
  campos e você dá o clique final de envio.
