# OperatorOS
## Project Blueprint — Interactive Multimodal Industrial Training & Video Reasoning System

---

# 1. Executive Summary

OperatorOS is an interactive multimodal AI system for industrial learning, manufacturing training, operational understanding, and contextual video reasoning.

The system allows a user to:
- Upload a YouTube URL or MP4 video
- Upload optional manuals, technical documentation, SOPs, PDFs, diagrams, or machine references
- Watch the video inside an AI-enhanced media player
- Pause at any frame
- Ask contextual questions about the exact current moment in the video
- Draw annotations directly on the paused frame
- Receive grounded multimodal AI responses using:
  - visual context
  - transcript context
  - document retrieval
  - annotation grounding
  - temporal understanding
- Trigger persistent segmentation and tracking overlays using SAM3

The system is designed as:
- a research system
- an internal laboratory tool
- a future extensible platform

This is NOT a generic chatbot.

This is:
- a temporally grounded
- spatially grounded
- annotation-aware
- multimodal industrial AI copilot

---

# 2. Project Identity

## Internal Research Codename
Project Blueprint

## Primary System/Product Name
OperatorOS

---

# 3. Core Vision

OperatorOS should feel like:

> “An AI expert that understands exactly what is happening in a manufacturing tutorial video at the precise moment the user pauses and asks a question.”

The experience should feel:
- interactive
- intelligent
- grounded
- context-aware
- visually aware
- temporally aware

The system should understand:
- what is on screen
- what the user annotated
- what was recently said in the video
- what the manuals/documents describe
- what object or component the user is referring to

---

# 4. Primary Research Innovation

The main innovation is NOT:
- video summarization
- document QA
- chatbot interaction

The primary innovation is:

## Simultaneous Grounding Across:
- video frame understanding
- temporal transcript understanding
- annotation-aware understanding
- document/manual retrieval
- persistent segmentation tracking

All inside one interactive system.

---

# 5. Existing Core Technology

The project is built around the existing repository:

GitHub Repository:
https://github.com/nontgcob/ragvlm

IMPORTANT:
The agentic AI MUST clone, inspect, understand, and reuse this repository before beginning implementation work.

This repository already contains critical infrastructure including:
- multimodal RAG
- annotation-aware interaction
- SketchVLM integration
- OpenRouter orchestration
- PDF/document ingestion
- retrieval systems
- embedding pipelines
- annotation UI logic
- visual reasoning flow
- multimodal prompting

The repository is NOT a reference example.

It is the CORE reasoning engine of OperatorOS.

---

# 6. Critical Architectural Rule

The system MUST:

```text
Build around RAGVLM
```

NOT:

```text
Rewrite RAGVLM
```

This distinction is extremely important.

The existing repository already solves:
- annotation-aware multimodal reasoning
- document-grounded visual reasoning
- multimodal retrieval orchestration

Rebuilding these systems introduces:
- regression risk
- wasted engineering time
- prompt degradation
- unnecessary architectural instability

---

# 7. High-Level User Experience Flow

```text
User uploads:
1. YouTube URL or MP4
2. Optional manuals/docs/PDFs

↓

System:
- Downloads/transcodes video
- Generates transcript + timestamps
- Indexes transcript + manuals into RAG

↓

Custom AI Video Player:
- User watches video
- Pause at any frame
- Ask question
- Draw annotations

↓

System captures:
- Current frame snapshot
- User annotations
- Timestamp
- Temporally relevant transcript window

↓

RAGVLM Service:
- Understand frame
- Understand annotations
- Understand transcript context
- Understand manuals/documents

↓

Retrieval:
- Retrieve relevant manual/document chunks

↓

VLM reasoning:
- Generate answer
- Generate optional visual overlays
- Generate optional segmentation prompt

↓

Optional:
- SketchVLM overlays
- SAM3 tracking
- Persistent segmentation overlays

↓

Playback resumes:
- Tracking overlays continue
- User annotations disappear
```

---

# 8. Core System Philosophy

OperatorOS is fundamentally:

```text
An event-driven multimodal orchestration platform
```

NOT:

```text
A chatbot
```

The architecture must revolve around:
- media events
- temporal states
- async processing
- multimodal context
- overlay rendering
- tracking persistence
- retrieval pipelines
- streaming inference

---

# 9. Architecture Overview

The system should use a modular microservice-oriented architecture.

---

# 10. High-Level Architecture Diagram

```text
Frontend Web Application
        ↓
Main Backend Orchestrator (FastAPI)
        ↓
------------------------------------------------
|               Internal Services              |
------------------------------------------------
|                                              |
|  RAGVLM Service                              |
|  - multimodal reasoning                      |
|  - annotation-aware VLM                      |
|  - SketchVLM                                 |
|  - retrieval orchestration                   |
|                                              |
|  SAM3 Service                                |
|  - segmentation                              |
|  - propagation                               |
|  - persistent tracking                       |
|                                              |
|  Video Processing Service                    |
|  - ffmpeg                                    |
|  - frame extraction                          |
|  - clip creation                             |
|  - temporal indexing                         |
|                                              |
|  Transcript Service                          |
|  - Whisper or equivalent                     |
|                                              |
|  Retrieval Service                           |
|  - vector search                             |
|  - transcript retrieval                      |
|                                              |
|  Async Queue / Workers                       |
|  - background jobs                           |
|  - streaming updates                         |
|                                              |
------------------------------------------------
```

---

# 11. RAGVLM Integration Strategy

RAGVLM should become a dedicated microservice.

Recommended structure:

```text
POST /ragvlm/infer
```

Instead of:

```python
from ragvlm import infer
```

Reasoning:
- modularity
- clean orchestration
- isolated debugging
- independent deployment
- scalability
- cleaner experimentation
- easier GPU/service management

---

# 12. Mandatory Repository Inspection

Before implementation begins, the agentic AI MUST:

## Clone and Inspect:
https://github.com/nontgcob/ragvlm

The agent MUST:
- understand architecture
- inspect dependencies
- inspect frontend/backend structure
- inspect annotation implementation
- inspect retrieval implementation
- inspect prompt orchestration
- inspect OpenRouter integration
- inspect SketchVLM pipeline

The repository MUST influence:
- framework decisions
- deployment decisions
- integration decisions
- dependency decisions

---

# 13. What MUST Be Reused

The following systems MUST be reused whenever possible:

- annotation drawing systems
- annotation parsing logic
- multimodal RAG
- OpenRouter orchestration
- SketchVLM integration
- PDF ingestion
- embedding pipelines
- vector retrieval systems
- conversation orchestration
- multimodal prompts
- annotation-aware reasoning flow

---

# 14. What MUST NOT Be Rewritten

The agentic AI MUST NOT:
- rewrite working annotation systems
- rewrite retrieval logic unnecessarily
- rewrite stable prompts unnecessarily
- rewrite OpenRouter orchestration
- rebuild multimodal reasoning from scratch
- replace existing RAG systems blindly

Instead:
- modularize
- expose APIs
- refactor carefully
- preserve stable logic

---

# 15. Frontend Requirements

The system is web-based.

Desktop-first initially.

Future:
- mobile browser support
- responsive adaptation

The frontend should contain:
- AI-enhanced video player
- annotation overlay layer
- segmentation overlay layer
- transcript interface
- contextual chat interface
- streaming responses
- loading indicators
- progress indicators
- overlay visibility toggles

---

# 16. Video Player Requirements

The custom player must support:
- play
- pause
- seek
- frame capture
- timestamp synchronization
- overlay rendering
- segmentation rendering
- annotation rendering

The player must support:
frame-accurate pause context capture.

---

# 17. Annotation System

The annotation system already exists in RAGVLM and should be reused.

Required annotation types:
- rectangle
- circle
- freehand sketch
- text labels

---

# 18. Annotation Behavior

IMPORTANT:

User-generated annotations:
- exist only during paused state
- disappear when playback resumes

They are:
- contextual
- temporary
- reasoning-focused

They are NOT persistent tracking overlays.

---

# 19. Segmentation Overlay Behavior

SAM3-generated overlays:
- persist during playback
- continue tracking objects
- include labels
- support hide/show toggles

If object leaves frame:
- overlay disappears

If object reappears:
- overlay should reappear

---

# 20. Transcript Strategy

The system MUST NOT feed the entire transcript every time.

Use:
timestamp-centered sliding context window.

Recommended strategy:

```text
Current timestamp:
04:32

Context window:
- 30 sec before
- current timestamp
- 15 sec after
```

This improves:
- relevance
- latency
- token efficiency
- grounding quality
- hallucination reduction

---

# 21. Transcript Pipeline

The transcript pipeline should:
- transcribe video
- align timestamps
- chunk temporally
- support fast timestamp retrieval

Recommended:
Whisper or equivalent.

---

# 22. Document Retrieval Strategy

Manuals/documents should:
- be chunked
- embedded
- indexed into vector database

Retrieval should consider:
- question semantics
- frame understanding
- annotation understanding
- transcript understanding

to retrieve relevant chunks.

---

# 23. Multimodal Reasoning Inputs

The VLM receives:
- current frame snapshot
- user annotations
- transcript context window
- retrieved manual chunks
- user question
- rolling conversation memory

The VLM should:
- reason spatially
- reason temporally
- reason contextually
- reason visually
- reference manuals when appropriate

---

# 24. Rolling Memory Strategy

Conversation memory persists only inside current session.

Use:
rolling window memory.

Do NOT:
feed full history every time.

Instead:
feed recent X interactions.

---

# 25. SketchVLM Behavior

SketchVLM should:
- generate visual explanations when useful
- create overlays directly inside player
- assist understanding visually

Do NOT implement:
full step-by-step tutorial systems yet.

---

# 26. SAM3 Integration

SAM3 runs locally.

The VLM automatically generates segmentation prompts.

Example:

User:
> “What is this lever?”

Internal generated prompt:
> “Track the metallic hydraulic lever near the control panel.”

SAM3 receives:
- frame
- prompt
- optional annotation guidance

---

# 27. SAM3 Tracking Pipeline

```text
Pause video
    ↓
Capture frame
    ↓
Generate segmentation prompt
    ↓
Run SAM3 propagation
    ↓
Generate future-frame overlays
    ↓
Show loading/progress
    ↓
Enable playback resume
    ↓
Tracking overlays persist
```

---

# 28. Critical UX Rule

DO NOT block answer generation waiting for SAM3.

Correct behavior:

```text
Answer immediately
Tracking asynchronously
```

NOT:

```text
Wait for tracking before answering
```

---

# 29. Streaming Responses

If supported:
- stream responses progressively
- show partial answers early

Target latency:
2–5 seconds for initial answer.

SAM3 may complete later.

---

# 30. Video Preprocessing

The system SHOULD leverage:
- frame extraction
- temporal indexing
- clip generation
- preprocessing
- timestamp indexing

The video is already fully available beforehand.

Exploit this advantage.

---

# 31. Recommended Stack Direction

The agentic AI should inspect RAGVLM before finalizing stack choices.

Recommended direction:

## Frontend
- Next.js
- TypeScript
- React
- Tailwind
- Canvas overlay rendering

## Backend
- FastAPI

## Workers
- Celery
- RQ
- or equivalent

## Video Processing
- ffmpeg

## Vector Database
- Qdrant
- FAISS
- or equivalent

## Realtime
- WebSockets

---

# 32. VLM Provider

VLM inference uses:
OpenRouter APIs.

Supported families include:
- GPT family
- Gemini family
- Claude family

The exact supported models should be determined by inspecting RAGVLM.

The architecture MUST preserve:
existing OpenRouter orchestration logic.

---

# 33. Hardware Resources

Available:
- NVIDIA RTX 4090
- SSH-accessible lab machine
- OpenRouter API access

SAM3 runs locally.

VLM reasoning runs via APIs.

---

# 34. Deployment Philosophy

The system should support:
- local research deployment
- future cloud deployment

Prefer:
Dockerized modular services.

---

# 35. Async/Event-Driven Design

The architecture should revolve around:
- playback events
- pause events
- inference events
- overlay updates
- streaming updates
- retrieval updates
- segmentation propagation

---

# 36. State Management

The system should track:
- current timestamp
- active overlays
- active segmentations
- playback state
- transcript window
- conversation window
- tracking progress
- streaming response state

---

# 37. Overlay System Separation

There are TWO fundamentally different overlay systems.

---

## A. User Annotations
- temporary
- human-generated
- disappear on resume

Purpose:
reasoning guidance.

---

## B. SAM3 Tracking Overlays
- model-generated
- persistent
- temporal
- survive playback

Purpose:
continuous visual tracking.

---

These MUST remain separate systems architecturally.

---

# 38. Future Roadmap

Potential future expansion:
- webcam/live feed
- mobile-first redesign
- collaborative sessions
- audio-event understanding
- predictive assistance
- tutorial chaptering
- AR overlays
- maintenance copilots

---

# 39. Recommended Development Phases

## Phase 1
- video ingestion
- transcript generation
- media player

## Phase 2
- RAGVLM microservice integration

## Phase 3
- annotation integration

## Phase 4
- timestamp-grounded retrieval

## Phase 5
- streaming responses

## Phase 6
- SAM3 integration

## Phase 7
- persistent overlays

## Phase 8
- optimization and UX polish

---

# 40. Important Engineering Constraints

The system MUST:
- remain modular
- remain debuggable
- avoid monolithic coupling
- separate inference services
- preserve working RAGVLM logic

---

# 41. Agentic AI Development Instructions

The coding agent MUST:

## Step 1
Clone and inspect:
https://github.com/nontgcob/ragvlm

## Step 2
Understand:
- architecture
- dependencies
- inference flow
- retrieval flow
- annotation implementation
- frontend/backend separation

## Step 3
Modularize reusable systems.

## Step 4
Expose reusable APIs/services.

## Step 5
Build OperatorOS around those services.

---

# 42. Critical Anti-Pattern Warnings

The agent MUST NOT:
- rewrite stable systems unnecessarily
- replace working prompts blindly
- rebuild multimodal reasoning from scratch
- tightly couple all services together
- create monolithic architecture

---

# 43. Important Conceptual Separation

OperatorOS is:
the orchestration platform.

RAGVLM is:
the multimodal reasoning engine.

This distinction is critical.

---

# 44. Final Product Experience Goal

The final system should feel like:

> “Pausing a manufacturing tutorial video and asking an AI expert about exactly what is happening on screen right now.”

with:
- temporal grounding
- visual grounding
- annotation grounding
- document grounding
- persistent tracking

all working together seamlessly.

---

# 45. Final Repository References

Primary repository:
https://github.com/nontgcob/ragvlm

Internal codename:
Project Blueprint

Primary platform name:
OperatorOS

---

# END OF SPECIFICATION