ブラウザ自動試験用フォルダ


```
mkdir playwright && cd playwright

curl -fsSL https://deb.nodesource.com/setup_26.x | sudo -E bash -

sudo apt install -y nodejs
npm init -y
npm install -D @playwright/test
npx playwright install --with-deps
```

バージョン確認
```
npx playwright test --version
```
