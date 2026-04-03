FROM mcr.microsoft.com/playwright:v1.52.0-noble

WORKDIR /srv
RUN npm init -y && npm install playwright@1.58.0 && npx playwright install chromium

EXPOSE 3000

CMD ["npx", "playwright", "run-server", "--host", "0.0.0.0", "--port", "3000"]
