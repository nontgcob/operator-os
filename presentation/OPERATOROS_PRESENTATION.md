# OperatorOS — Project Overview

**Who this is for:** anyone new to the project — teammates, reviewers, stakeholders, or demo audiences  
**What you'll learn:** what OperatorOS does, how someone uses it, and how the system fits together behind the scenes

---

## 1. What is OperatorOS?

OperatorOS is an **AI assistant built around video**. It is designed for industrial training, machine walkthroughs, and operational learning — situations where someone is watching a tutorial and needs help understanding *exactly what they are looking at*.

It is **not** a generic chatbot. It understands:

- **What is on screen** at the moment you paused
- **What you pointed at** with your drawings and highlights
- **What was just said** in the video (spoken words near that moment)
- **What your manuals say** if you uploaded reference documents
- **How to explain the answer visually** by drawing back onto the video

The core idea in one sentence:

> Pause a training video, circle the part you care about, ask a question, and get a grounded answer — with optional visual overlays and object tracking.

---

## 2. What can someone do with it?

| What you can do | What it means |
|-----------------|---------------|
| **Load a video** | Upload a video file from your computer, or paste a YouTube link |
| **Watch and pause** | Play the video in the browser and stop at any moment |
| **Draw on the video** | Highlight areas with rectangles, arrows, pen strokes, text, and more |
| **Ask questions** | Chat about what you are looking at right now |
| **Attach manuals** | Upload PDFs, Word docs, or text files so answers can reference real documentation |
| **Get visual answers** | The AI can reply with text *and* draw shapes on the video to show what it means |
| **Track an object** | Optionally follow the thing you highlighted as the video continues *(still being polished)* |

---

## 3. How the system is organized

OperatorOS is made of several specialized parts that work together. Think of it like a team: one part handles the screen you see, another prepares videos, another reads manuals, another powers the AI, and another can track objects in the footage.

```mermaid
flowchart TB
    subgraph User["What you see in the browser"]
        UI[Video player, drawing tools, and chat]
    end

    subgraph Hub["Central coordinator"]
        ORCH[Connects everything and keeps the session in sync]
    end

    subgraph Video["Video preparation"]
        VS[Downloads or saves videos, creates transcripts, prepares frames]
    end

    subgraph AI["AI & document search"]
        DOCS[Reads and searches uploaded manuals]
        BRAIN[Builds the full question and calls the AI model]
    end

    subgraph Tracking["Object tracking"]
        TRACK[Follows highlighted objects through the video]
    end

    subgraph Storage["Saved data"]
        DISK[(Videos, transcripts, and frame snapshots)]
        MANUALS[(Uploaded manuals and guides)]
    end

    subgraph External["Outside services"]
        YT[YouTube]
        CLOUD[Cloud AI provider]
    end

    UI --> ORCH
    ORCH --> VS
    ORCH --> DOCS
    ORCH --> BRAIN
    ORCH --> TRACK
    VS --> DISK
    VS --> YT
    DOCS --> MANUALS
    BRAIN --> CLOUD
    TRACK --> DISK
```

### What each part does (plain language)

| Part | Role |
|------|------|
| **The app (browser)** | Where you watch video, draw annotations, upload manuals, and chat |
| **Central coordinator** | Routes your actions to the right backend and streams results back live |
| **Video preparation** | Gets the video ready: saves it, transcribes speech, extracts still frames |
| **Document search** | Indexes uploaded manuals and finds the most relevant sections for your question |
| **AI brain** | Combines the paused frame, your drawings, transcript, manuals, and question — then asks the AI |
| **Object tracking** | Takes your highlight box and tries to follow that object forward in the video |

---

## 4. Step 1 — Loading a video

Before anyone can ask questions, the video needs to be loaded and prepared behind the scenes.

```mermaid
flowchart LR
    subgraph Input
        FILE[Video file from computer]
        LINK[YouTube link]
    end

    subgraph App
        FE[The app]
    end

    subgraph Prepare["Video preparation"]
        direction TB
        V1{Where did it come from?}
        V2[Save the video file]
        V3[Download from YouTube and save title]
        V4[Transcribe spoken words]
        V5[Extract still frames from the video]
    end

    subgraph Storage
        S[(Everything saved and ready to use)]
    end

    FILE --> FE --> V1
    LINK --> FE
    V1 -->|upload| V2
    V1 -->|youtube| V3
    V2 --> V4
    V3 --> V4
    V4 --> V5 --> S
    S --> FE
```

### What gets prepared for each video

| Prepared item | Why it matters |
|---------------|----------------|
| **The video file** | So you can play it back in the browser |
| **Video title** | Shown in the app and given to the AI for extra context (especially from YouTube) |
| **Transcript** | A text record of what was said, with timestamps — so the AI knows what was being explained at that moment |
| **Frame snapshots** | Still images pulled from the video — used for object tracking when needed |

### YouTube videos

If someone pastes a YouTube link, the system downloads the video, reads its title, transcribes the audio, and prepares everything the same way as an uploaded file. The title from YouTube (e.g. *"How to operate the CNC panel"*) helps the AI understand what kind of video it is looking at.

---

## 5. Step 2 — Pause, highlight, and get ready to ask

When someone pauses the video, the app captures the full context of that moment.

```mermaid
flowchart TB
    PAUSE[User pauses the video]
    TIME[App records exactly where in the video they stopped]
    SPEECH[App loads what was said nearby]
    DRAW[User draws on the video — boxes, arrows, labels, etc.]
    TITLE[Video title is shown and included as context]

    PAUSE --> TIME --> SPEECH
    PAUSE --> DRAW
    PAUSE --> TITLE
```

**Your drawings** are saved as structured information (not just pixels on screen), so the AI knows *what* you highlighted and *where* on the frame.

**Nearby speech** gives the AI conversational context — for example, if the instructor just said *"this valve controls pressure,"* that helps ground the answer.

**The video title** adds one more layer of context, especially useful for YouTube tutorials with descriptive titles.

---

## 6. Step 3 — Ask a question

When the user sends a question, **two things can happen at the same time**:

1. **Get an AI answer** — text plus optional drawings on the video  
2. **Track the highlighted object** — follow what they boxed as the video moves forward *(optional, still being improved)*

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant App as The app
    participant Hub as Central coordinator
    participant Video as Video preparation
    participant Docs as Document search
    participant AI as Cloud AI
    participant Track as Object tracking

    User->>App: Pause, draw, type question, click Send

    App->>App: Capture the paused frame

    par Path A — AI answer
        App->>Hub: Send question + frame + drawings + transcript + manuals + title
        Hub->>Docs: Search attached manuals (if any)
        Docs-->>Hub: Most relevant manual sections
        Hub->>AI: Full question with all context
        AI-->>Hub: Answer streaming back
        Hub-->>App: Answer arrives live
        App->>User: Text reply + AI-drawn shapes on the video
    and Path B — Object tracking (optional)
        App->>Hub: Start tracking from user's highlight box
        Hub->>Track: Follow this object from here onward
        Track->>Track: Analyze upcoming frames
        loop As the video progresses
            Track-->>Hub: Updated outline of the object
            Hub-->>App: Live tracking overlay
            App->>User: Green outline follows the object
        end
    end
```

---

## 7. What information does the AI actually receive?

When you ask a question, the AI does not just see your typed words. It gets a rich picture of the moment you paused on.

```mermaid
flowchart TB
    subgraph Images["What the AI sees visually"]
        I1[The paused video frame]
        I2[Optional: a snapshot with your drawings visible on top]
    end

    subgraph Context["What the AI reads as text"]
        T1[Your question]
        T2[Your drawings — where and what you highlighted]
        T3[What was said in the video nearby]
        T4[Relevant sections from uploaded manuals]
        T5[The video title]
    end

    subgraph Output["What the AI sends back"]
        O1[Written answer]
        O2[Visual overlays drawn on the video]
    end

    I1 --> AI[Cloud AI model]
    I2 --> AI
    T1 --> AI
    T2 --> AI
    T3 --> AI
    T4 --> AI
    T5 --> AI
    AI --> O1
    AI --> O2
    O2 --> App[Shown on the video in the app]
```

### Why this matters

Most chat tools only see your words. OperatorOS also sees **the frame**, **your highlights**, **what was being said**, and **what the manual says** — all tied to the exact moment you paused. That is why answers can be specific instead of generic.

---

## 8. Uploading manuals and reference documents

Operators can upload technical documents — manuals, SOPs, safety guides — so answers are grounded in real documentation, not just the video.

```mermaid
flowchart LR
    UP[User uploads a manual]
    READ[System reads and indexes the document]
    STORE[(Saved for future questions)]

    ASK[User asks a question with manuals attached]
    SEARCH[System finds the most relevant sections]
    ANSWER[Those sections are included in the AI's context]

    UP --> READ --> STORE
    ASK --> SEARCH --> ANSWER
```

You can attach or detach documents for each question — so a general walkthrough question might not need a manual, but a safety question can pull from the official SOP.

---

## 9. Object tracking (work in progress)

Object tracking lets the system **follow the thing you highlighted** as the video continues playing.

```mermaid
flowchart LR
    subgraph Input
        BOX[User draws a box around an object]
        Q[User asks a question]
    end

    subgraph Tracking
        CLIP[Look at the video from this point forward]
        FOLLOW[Identify and follow the highlighted object]
        OUTLINE[Draw an outline on each frame]
    end

    subgraph Result
        OL[Green outline follows the object on screen]
    end

    BOX --> CLIP
    Q --> CLIP
    CLIP --> FOLLOW --> OUTLINE --> OL
```

**Good to know:**

- Tracking runs **at the same time** as the chat answer — you do not have to wait for one to finish before getting the other.
- This feature is integrated but still being polished for speed and reliability in the full app experience.

---

## 10. The full journey at a glance

```
┌──────────────────────────────────────────────────────────────────────┐
│                         OPERATOROS — END TO END                       │
├──────────────────────────────────────────────────────────────────────┤
│ LOAD A VIDEO                                                          │
│   Upload a file  ──►  video is saved, speech transcribed, frames ready │
│   Paste YouTube  ──►  same preparation, plus title from YouTube        │
├──────────────────────────────────────────────────────────────────────┤
│ USE THE VIDEO                                                         │
│   Play  ──►  pause at the moment you care about                       │
│   Draw on screen to show exactly what you mean                        │
│   Optionally attach manuals for reference                             │
├──────────────────────────────────────────────────────────────────────┤
│ ASK A QUESTION (two things happen in parallel)                        │
│                                                                       │
│   A) AI answer                         B) Object tracking (optional)  │
│      • sees the paused frame              • starts from your highlight│
│      • reads your drawings                • follows the object ahead  │
│      • knows what was said                • draws a live outline      │
│      • searches attached manuals                                      │
│      → text answer + visual overlays on the video                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 11. Where things stand today

| Feature | Status |
|---------|--------|
| Load videos (file upload or YouTube) | Working |
| Watch and pause in the browser | Working |
| Draw annotations on the video | Working |
| Ask questions and get streaming answers | Working |
| AI draws visual overlays on the video | Working |
| Speech transcription | Working |
| Video title shown and used as context | Working |
| Upload manuals and search them for answers | Built — full end-to-end testing still recommended |
| Object tracking in the video | Integrated — performance and user experience still being improved |

---

## 12. Summary for presenters

**Elevator pitch:**

OperatorOS lets someone pause an industrial training video, circle the exact part they mean, and ask a question. The system combines what is on screen, what was said nearby, what the manuals say, and what the user drew — then answers with text and visual overlays. Optionally, it can track that object forward in the video.

**Why it is different:**

Most tools do video *or* documents *or* chat. OperatorOS brings all of them together at a **specific moment in the video**, with **visual highlights** that show exactly what the user is asking about. That matches how people actually learn on the job — they pause, point, and ask.

**How it works in one sentence:**

You pause and highlight something on a training video; the app gathers everything relevant about that moment and asks an AI that can both explain in words and draw on the screen to show you what it means.

---

## 13. Demo walkthrough (for live presentations)

A simple story to tell while showing the app:

1. **Load** — Upload a machine tutorial or paste a YouTube link. Wait for the video to appear.
2. **Watch** — Play until something confusing appears on screen.
3. **Pause & point** — Stop the video and draw a box or arrow on the part you do not understand.
4. **Ask** — Type something like *"What does this do?"* or *"Is this the safety interlock?"*
5. **See the answer** — Read the reply in chat and watch the AI draw on the video to explain.
6. **Optional** — Upload a manual first, then ask a question that references it. Show how the answer pulls from the document.

That single flow — pause, point, ask — is the heart of OperatorOS.
