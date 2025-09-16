# 🤖 GenAI Requirements & BDD Generator (POC)

> 📝 **Meeting transcript → Requirements + Test Cases → Approve → Export to Jira**

This repository is a **Proof of Concept (POC)** that demonstrates how to use GenAI to automatically convert a meeting transcript (from Microsoft Teams) into:

- 📋 **Requirements (user stories)**
- ✅ **BDD / Gherkin test cases**
- 🖥️ **Manual approval UI**
- 📤 **CSV export for Jira import**

---


## 🔐 API Key Setup

This project requires an OpenAI API key.

1. Go to [https://platform.openai.com/account/api-keys](https://platform.openai.com/account/api-keys)
2. Click **Create new secret key** and copy it (`sk-...`)
3. In the project root, create a file named `.env` with:

```env
OPENAI_API_KEY=sk-YOUR-OWN-KEY-HERE

---

## ⚙️ Architecture

```mermaid
flowchart TD
    A[Meeting Transcript .vtt] --> B(generate_req_bdd.py)
    B -->|OpenAI API| C[Requirements + Test Cases]
    C --> D[(repo.db)]
    C --> E[output.json]
    D --> F[Flask UI app.py]
    F -->|Approve| D
    D --> G[export_csv.py]
    G --> H[CSV for Jira]