# The Morning Gazette

A personal newspaper in the style of a 1920s big-city broadsheet. Every morning it gathers the day's news from RSS feeds,
asks Google's Gemini to rewrite the headlines in period style, and prints a one-page front page with a lead story,
a "Stop Press" column and coloured section boxes. Every story shows where it came from.

Built as a first coding project, with the help of AI assistants.

## What it does

- Collects news on Sydney and Australia, finance and markets, sport, and health and nutrition
- Rewrites headlines and summaries in 1920s style with Gemini, using only the facts in the original story
- Builds a one-page HTML front page and a PDF, and can send it to your printer
- Can run by itself every morning on a Mac

## Setup (Mac)

1. Install the one library it needs:

   ```
   python3 -m pip install -U google-genai
   ```

2. Get a free Gemini API key at https://aistudio.google.com/apikey

3. Run the program once. It asks for your key and saves it in a private file called `.env` next to the program.
   That file is never uploaded anywhere. Do not share your key or commit `.env`.

## Use it

| Command | What it does |
|---|---|
| `python3 newspaper.py --open` | Builds today's paper and opens it in your browser |
| `python3 newspaper.py --print` | Builds it and sends it to your default printer (needs Google Chrome) |
| `python3 newspaper.py --schedule 07:00` | Prints the paper every day at that time |
| `python3 newspaper.py --unschedule` | Stops the daily printing |
| `python3 newspaper.py --demo` | Tests the layout with sample stories, no internet needed |
| `python3 newspaper.py --no-ai` | Real news with the original headlines, no Gemini |

## Make it yours

Open `newspaper.py` and edit the **SETTINGS** block at the top: the paper's name, the sections, how many stories each one
shows, and the RSS feeds the news comes from.

## Notes

- News text belongs to the original publishers. This project only contains code, and each story links back to its source.
- Gemini is told to use only the facts it is given, but check the original source for anything important.
- Health and finance items are news reports, not medical or financial advice.
