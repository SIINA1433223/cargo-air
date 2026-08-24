# 航空货运查询系统（GitHub Pages 版）

这是航班/货运查询系统的 **GitHub Pages 静态网页版**，不需要国内云服务器，打开就是网页、不会下载。

---

## 已上传即生效

目录里的 `index.html` 已经注入了当前真实飞书数据，直接按下面步骤启用 Pages 即可访问。

---

## 部署步骤（跟着点，5 分钟搞定）

### 1. 创建 GitHub 仓库

1. 打开 [https://github.com/new](https://github.com/new)
2. 仓库名填 `cargo-query`（也可以随便取）
3. 下面的 **Public / Private 选择要特别注意**：
   - 选 **Public**：同事不用登录 GitHub 就能直接打开，网页访问最方便。**但 `index.html` 里的业务数据会暴露在公网**。
   - 选 **Private**：数据不对外公开，但 GitHub 免费账户的私有 Pages **只有仓库成员能访问**，同事必须登录 GitHub 且被加为仓库成员。
4. 点击最下面的 **Create repository**

> 建议：如果只是内部同事看，且数据不敏感，选 Public 最省事。

### 2. 上传本目录所有文件

创建完仓库后，在仓库页面：

1. 点击 **uploading an existing file**（上传现有文件）
2. 把下面文件/文件夹全部拖进去：
   - `index.html`
   - `template.html`
   - `scripts/` 文件夹
   - `.github/` 文件夹
   - `README.md`
3. 点击 **Commit changes**

### 3. 开启 GitHub Pages

1. 在仓库顶部点 **Settings**
2. 左侧找到 **Pages**
3. 在 **Source** 里选择：
   - **Deploy from a branch**
   - Branch: **main**
   - Folder: **/(root)**
4. 点 **Save**
5. 等 1~2 分钟，刷新页面，会出现绿色提示：
   - `Your site is live at https://你的用户名.github.io/仓库名/`
6. 把这个链接发给同事即可

---

## 数据自动同步（可选）

`index.html` 现在是静态快照，不会自动刷新。想让网页每小时自动拉最新飞书数据，按下面做：

### 添加 Secrets

1. 仓库顶部 **Settings**
2. 左侧 **Secrets and variables** → **Actions**
3. 点 **New repository secret**，逐个添加：
   - `FEISHU_APP_ID`：你的飞书应用 ID
   - `FEISHU_APP_SECRET`：你的飞书应用 Secret
4. 如果你的多维表格不是默认那张表，再添加：
   - `FEISHU_APP_TOKEN`（默认已内置，一般不用改）
   - `FEISHU_TABLE_ID`（默认已内置，一般不用改）
   - `FEISHU_VIEW_ID`（默认已内置，一般不用改）

### 手动触发同步

1. 仓库顶部点 **Actions**
2. 左侧点 **Sync Feishu Data**
3. 右侧点 **Run workflow** → **Run workflow**
4. 等 1 分钟左右，它会自动拉飞书数据并更新 `index.html`

### 自动定时同步

`.github/workflows/sync.yml` 已配置每小时自动同步一次。只要 Secrets 填了，后续不用管。

---

## 常见疑问

**Q：为什么 GitHub Pages 不会下载？**
GitHub Pages 是纯静态网页托管，返回的响应头就是正常的 `text/html`，不会像阿里云 FC 那样强制加 `attachment` 下载头。

**Q：数据更新后要多久网页才变？**
GitHub Pages 缓存较短；Actions 更新 `index.html` 并 push 后，通常 1~2 分钟内生效。

**Q：同事在国内访问会不会很慢？**
GitHub Pages 国内部分地区可能慢，偶尔抽风。如果你之后想换方案，可以再用 Cloudflare Pages / Vercel / Netlify 等国外静态托管，部署方式完全一样。

**Q：这个页面有访问口令吗？**
GitHub Pages 是静态托管，做不了后端登录。如果你需要访问口令保护，只能依赖 GitHub 仓库私有 + 添加仓库成员。
