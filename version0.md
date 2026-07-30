# OperatorOS V0

Description:

In this version 0, we implemented 2 kinds of RAG: pure text-based RAG and Multimodal RAG.

However, it doesn't seem to work so well so we are thinking of pivoting to just inference models like Gemini and sending the PDF as an attachment directly as I think that the current intermediate processing pipeline is not the way to go and am running out of ideas for improvements to the current system in the way that we are doing it. This for now is a temporary and naive fix but there is a possibility of it turning into a permanent fix if the outcome turns out to work well which we will be discovering very soon after the this commit.

This commit marks the point of version 0 of the OperatorOS project.