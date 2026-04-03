FROM mcr.microsoft.com/playwright:v1.52.0-noble

RUN npx playwright install chromium

EXPOSE 3000

CMD ["npx", "playwright", "run-server", "--host", "0.0.0.0", "--port", "3000"]
