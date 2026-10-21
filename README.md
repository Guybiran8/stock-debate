# Stock Debate

An AI research tool where Grok researches a stock, Claude and ChatGPT argue bull vs bear, and Grok judges the evidence.

Ever since I started trading, I've had an issue with researching stocks. I didn't have the patience to read everything or analyze the same graph for 20 minutes for a move that wouldn't even hit. So instead I would watch TikTok or Instagram, listening to what other people said without checking the evidence myself.

So I decided to make a tool that does the research with you and teaches you along the way. It gathers all the relevant info on a stock based on whether you're trading short-term or holding long-term: earnings, deals, reports, lawsuits, X posts and everything else that matters. Then Claude and ChatGPT debate the bull and bear case. I picked two AIs and a judge so they can check each other's mistakes and argue about the information, which makes it more accurate. When I started, I used only one AI, and aside from the information not being accurate, some of it was made up. It claimed Palantir was worth $3 billion, when it was actually worth over $400 billion, and named deals with other companies that it couldn't back up with any source.

## Setup

1. Install Python 3, then install the requirements:

   ```
   pip install -r requirements.txt
   ```

2. Create a file named `.env` in the project folder with these four keys:

   ```
   ANTHROPIC_API_KEY=
   OPENAI_API_KEY=
   XAI_API_KEY=
   POLYGON_API_KEY=
   ```

3. Run it:

   ```
   python main.py
   ```
