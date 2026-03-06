# I-ADOPT Variable Decomposition Service

This project provides a simple web interface for **decomposing variable definitions into the I-ADOPT ontology structure** and **visualizing the result**.

The system combines:

* a **Python backend** that runs the LLM pipeline
* a **JavaScript frontend** that visualizes the generated RDF/Turtle (TTL)

The user can:

1. enter a variable definition in plain text
2. click **Decompose**
3. inspect the **raw LLM output**
4. inspect **JSON schema validation errors**
5. view the **generated TTL**
6. click **Visualize**
7. see the **I-ADOPT diagram**

---

# Project Architecture

The project consists of two main parts.

## Backend (FastAPI)

Location:

```
backend/
```

The backend is responsible for running the **LLM decomposition pipeline**.

It performs the following steps:

1. receives a **variable definition** from the frontend
2. builds an **LLM prompt**
3. calls the **OpenRouter API**
4. extracts and parses the **JSON output**
5. validates the output using a **JSON Schema**
6. enriches components with **Wikidata URIs**
7. converts the structured result to **RDF/Turtle (TTL)**
8. returns the results to the frontend

The backend exposes a simple API:

```
POST /decompose
```

Input:

```
{
  "definition": "My system is measuring air temperature at a height of 1.7m"
}
```

Output:

```
{
  raw_llm_output,
  parsed_json,
  validation_errors,
  ttl
}
```

---

## Frontend (Vite + JavaScript)

Location:

```
frontend/
```

The frontend provides the **interactive user interface**.

It allows the user to:

1. enter a variable definition
2. send it to the backend
3. display:

   * raw LLM output
   * validation errors
   * generated TTL
4. visualize the TTL using the **I-ADOPT visualizer**

The visualization is rendered as an **SVG diagram**.

---

# Running the Project

The easiest way to run the project is using **Docker Compose**.

This will start:

* the **backend container**
* the **frontend container**

---

# 1. Clone the Repository

```bash
git clone (https://github.com/ArvinRastegar/i-adopt-service-website.git)
cd i-adopt-service-website
```

---

# 2. Create a `.env` File

Create a file called:

```
.env
```

in the **project root**.

Inside the file add:

```
OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxxxxxxxxxx
```

Replace the key with your **OpenRouter API key**.

This key is used by the backend to call the LLM.

---

# 3. Build and Run the Project

From the project root run:

```bash
docker compose up --build
```

Docker will:

1. build the backend image
2. build the frontend image
3. start both containers

---

# 4. Open the Application

Frontend:

```
http://localhost:5173
```

Backend API:

```
http://localhost:8000/docs
```

---

# How to Use the Application

1. Enter a **variable definition** in the first textbox.

Example:

```
My system is measuring air temperature at a height of 1.7m
```

2. Click **Decompose**

You will see:

* raw LLM output
* schema validation results
* generated TTL

3. Click **Visualize**

The I-ADOPT diagram will appear below.

---

# Stopping the Application

Press:

```
CTRL + C
```

or run:

```bash
docker compose down
```

---

# Project Structure

```
i-adopt-service-website
│
├── backend
│   ├── app
│   │   └── main.py
│   ├── data
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend
│   ├── src
│   ├── css
│   ├── index.html
│   └── Dockerfile
│
├── docker-compose.yml
└── README.md
```

---

# Summary

This project connects an **LLM-based decomposition service** with the **I-ADOPT visualizer**.

The backend performs the semantic decomposition and generates RDF.

The frontend provides a simple interface to explore the results and visualize the variable structure.