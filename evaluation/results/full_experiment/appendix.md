% =============================================================
%  Appendix A — User Perception Survey
%  Paste this into your thesis .tex file, after \appendix
% =============================================================


\begin{appendices}
\renewcommand{\clearpage}{}
\renewcommand{\cleardoublepage}{}
\titlespacing*{\chapter}{0pt}{1cm}{1cm}

\chapter{Pipeline Walkthrough: FreshCo C0 Run}
\label{appendix:pipeline}

This appendix traces a complete pipeline execution for the FreshCo client
through the full framework configuration (C0), to ground the quantitative
results of Chapter~\ref{ch:evaluation} in a concrete example. 


\section{Client Request}

The pipeline receives the following payload $P$ at its intake endpoint,
in the natural-language format:

\begin{quote}
\textit{``FreshCo, a food-distribution company, requires a cold-storage
warehouse in Southern Europe to support its distribution operations.
Monthly budget: EUR~18{,}000. The facility must meet food-grade
compliance requirements. This request is high priority.''}
\end{quote}

\noindent Stage~1 of Algorithm~\ref{alg:task-normalization} extracts the
required contextual metadata $M$ from the payload:

\begin{itemize}[nosep]
    \item \textbf{Client:} FreshCo (food-distribution company)
    \item \textbf{User context:} authorised tenant account
    \item \textbf{Submission channel:} REST intake endpoint
    \item \textbf{Declared priority:} high
    \item \textbf{Declared constraints:} budget EUR~18{,}000/month,
          cold storage, food-grade compliance
\end{itemize}

\noindent All required fields are present, so the payload is admitted
to Stage~2 (content filtering) rather than rejected. Stage~2 detects
no policy violations, and the request proceeds to structuring.


\section{Task Normalization}

In Stage~3 the Supervisor Agent invokes an LLM to emit a
\texttt{stig:TaskSpec}. For the FreshCo request the TaskSpec
instance contains:

\begin{itemize}[nosep]
    \item \textbf{task\_id:}
          \texttt{stig:task/freshco-cold-storage-es-2026}
    \item \textbf{description:} ``Locate and contract a cold-storage
          warehouse in Southern Europe for food distribution, budget
          EUR~18{,}000/month, food-grade compliance required.''
    \item \textbf{required\_capabilities:}
          \{\texttt{transcription},
            \texttt{form\_filling},
            \texttt{warehouse\_matching},
            \texttt{deal\_estimation},
            \texttt{compliance\_check},
            \texttt{proposal\_generation}\}
    \item \textbf{tags:} \{``food-distribution'', ``cold-storage'',
          ``southern-europe'', ``food-grade'', ``HACCP''\}
    \item \textbf{risk\_level:} \texttt{high}
    \item \textbf{priority:} \texttt{high}
    \item \textbf{requires\_human\_approval:} \texttt{True}
\end{itemize}

The following orchestration identifiers
are attached to the TaskSpec when it is persisted to the graph marker
store:

\begin{itemize}[nosep]
    \item \textbf{Pipeline ID:}
          \texttt{eb595973-d0cf-49b1-8e13-8e92dbf283f9}
    \item \textbf{Run ID:}
          \texttt{14c503ab-a83d-4647-b48d-b13d56d57295}
    \item \textbf{Tasks generated from TaskSpec:} 12
    \item \textbf{Strategy emitted:} True
    \item \textbf{Ethics-check scheduled:} True
\end{itemize}


\section{Stigmergic Task Assignment via ICNP}
\label{appendix:icnp}

Task assignment follows Algorithm~\ref{alg:icnp}. For each derived task
the system issues a CFP; each registered agent computes its stimulus
score $S$ (Eq.~\ref{eq:stimulus}) against its dynamic response
threshold $\theta_i$ (Eq.~\ref{eq:threshold}) and, if admitted,
generates a bid score $B$ (Eq.~\ref{eq:bid-score}). The winning bid
produces a \texttt{stig:AffordanceMarker}. The two competitive rounds
for this run are shown below.


\subsection{Round 1 -- Warehouse Matching (3-way bid)}

CFP parameters: required capability \texttt{warehouse\_matching},
priority \texttt{high}, initial stimulus $s_0 = 1.0$.

\begin{table}[H]
\centering
\small
\begin{tabular}{llll}
\toprule
Agent & Bid score $B$ & Region tag & Outcome \\
\midrule
warehouse-south   & 0.6988 & Southern Europe (Milan, Bologna, Rome) & Winner \\
warehouse-central & 0.3387 & DACH + France (Munich, Lyon, Paris)    & Runner-up \\
warehouse-north   & 0.3197 & Scandinavia (Gothenburg)               & Runner-up \\
\bottomrule
\end{tabular}
\end{table}

\begin{itemize}[nosep]
    \item \textbf{Bid delta (winner $-$ runner-up):} 0.3600
    \item \textbf{Expected winner:} \texttt{warehouse-south}
          (FreshCo requests Southern Europe)
    \item \textbf{Actual winner:} \texttt{warehouse-south} (correct)
\end{itemize}

\noindent The margin is explained by tag overlap on the
\texttt{southern-europe} tag; the capability-match term of $B$
(Eq.~\ref{eq:bid-score}) contributes equally to all three bidders.


\subsection{Round 2 -- Deal Estimation (2-way bid)}

CFP parameters: required capability \texttt{deal\_estimation},
priority \texttt{high}. The two competing estimators differ only in
their secondary capability (\texttt{financial\_analysis} vs.\
\texttt{timeline\_analysis}) and optimisation strategy.

\begin{table}[H]
\centering
\small
\begin{tabular}{llll}
\toprule
Agent & Bid score $B$ & Strategy & Outcome \\
\midrule
cost-estimator  & 0.6955 & Cost-optimised  & Winner \\
speed-estimator & 0.3196 & Speed-optimised & Runner-up \\
\bottomrule
\end{tabular}
\end{table}

\begin{itemize}[nosep]
    \item \textbf{Expected winner:} \texttt{cost-estimator}
          (FreshCo has high priority but a firm EUR~18{,}000/month
          budget; the cost-optimised strategy aligns with the
          TaskSpec's budget constraint).
    \item \textbf{Actual winner:} \texttt{cost-estimator} (correct).
\end{itemize}


\section{Plan Generation, SHACL Validation, and DAG Construction}

The Graph Builder Agent synthesises the affordance markers from both
bidding rounds into an execution graph, then submits it to the
validation pipeline.

\paragraph{SHACL validation (Algorithm~\ref{alg:shacl}).}
Both the \texttt{pyshacl} reasoner and the programmatic validator
approve the plan on the first pass:

\begin{itemize}[nosep]
    \item \textbf{C1 -- Structural completeness:} passed (all 12 task
          nodes have an assigned agent, a non-empty description, and
          a non-empty capability set).
    \item \textbf{C2 -- Graph integrity:} passed (no dangling edges;
          Kahn's algorithm confirms acyclicity).
    \item \textbf{C3 -- Capability alignment:} passed.
    \item \textbf{C4 -- Ontology conformance:} passed.
    \item \textbf{final\_conforms:} True.
    \item \textbf{Iterative refinements applied:} 0.
\end{itemize}

\paragraph{DAG construction (Algorithm~\ref{alg:dag}).}
\begin{itemize}[nosep]
    \item \textbf{Tasks:} 12
    \item \textbf{Maximum topological depth:} 5
    \item \textbf{Parallel phases $\{\Phi_k\}$:}
          $|\Phi_0| = 1$ (Call Transcription root),
          $|\Phi_1| = 2$,
          $|\Phi_2| = 5$ (warehouse + estimator bidding winners,
          compliance fan-in),
          $|\Phi_3| = 2$,
          $|\Phi_4| = 1$ (Proposal Template leaf).
    \item \textbf{Critical path length:} 5 tasks (intake
          $\rightarrow$ form fill $\rightarrow$ warehouse match
          $\rightarrow$ compliance $\rightarrow$ proposal).
    \item \textbf{Fan-out / fan-in nodes:} Deal Summary acts as the
          primary fan-out, Proposal Template is the terminal
          fan-in join.
\end{itemize}


\section{Human Validation and Approval}
\label{appendix:human-validation}

 Because the TaskSpec carries
\texttt{risk\_level = high} and
\texttt{requires\_human\_approval = True}, both triggering conditions
at Section~\ref{sec:human-validation} fire and the plan is routed to
the \emph{mandatory human sign-off} workflow rather than directly to
user plan validation.

\paragraph{Mandatory sign-off.} The approver is presented with the
full plan context (task assignments, dependency structure, SHACL
report, risk factors) and approves the plan. The structured sign-off
record contains:

\begin{itemize}[nosep]
    \item \textbf{Approver:} pipeline operator
    \item \textbf{Authorisation level:} tenant-admin
    \item \textbf{Decision:} approved
    \item \textbf{Rationale:} ``Plan satisfies FreshCo's cold-storage
          and budget constraints, winning agent holds required
          food-grade certifications.''
\end{itemize}

\paragraph{User plan validation.} After sign-off the plan is
presented to the user, who accepts it without modification. 


\section{Execution, Adaptation, and Failure Handling}

\subsection{Ethics-Check Gate}

Immediately before execution, the Ethics-Check Agent
(Section~\ref{sec:execution}) performs its four domain-agnostic checks:

\begin{itemize}[nosep]
    \item \textbf{No hallucinated data:} passed.
    \item \textbf{No unfair bias:} passed.
    \item \textbf{No sensitive-data leakage:} passed.
    \item \textbf{No unsafe approvals:} passed (mandatory human
          sign-off present).
    \item \textbf{Veto:} none.
\end{itemize}


\subsection{Domain Compliance Check}

A domain-specific Compliance agent then audits the plan against
FreshCo's business rules. Unlike the domain-agnostic Ethics-Check
Agent, the Compliance agent
(Table~\ref{tab:agent-inventory}) is deterministic and rule-based.
It evaluates each candidate warehouse-client pairing against the
five rules defined in Chapter~\ref{ch:evaluation}, cold storage,
hazmat certification, security level, square-footage range, and
budget compliance, and returns structured pass/fail results scoped
to the upstream-suggested pairing when possible.

\begin{itemize}[nosep]
    \item \textbf{Passed:} False
    \item \textbf{Blocks:} 2 -- two warehouses in the inventory
          (Bavaria Logistics Hub, Centro Logistico Bologna) fail the
          cold-storage rule and are blocked for any HACCP-tagged
          TaskSpec.
    \item \textbf{Warnings:} 4
    \item \textbf{Scoped to winning assignment:} True -- the selected
          \texttt{warehouse-south} passes all five rules, blocks
          apply only to alternatives that would have been selected
          under relaxed constraints.
\end{itemize}

\noindent Because the blocks do not affect the winning assignment,
execution proceeds.


\subsection{Execution Summary}

\begin{itemize}[nosep]
    \item \textbf{Tasks completed:} 12/12
    \item \textbf{Tasks failed:} 0
    \item \textbf{Strategist deltas applied:} 0 (no runtime
          replanning required).
    \item \textbf{End-to-end latency:} 440.6\,s. This run sits in the
          upper tail of the C0 distribution (median 210\,s,
          IQR 177--239\,s); the inflated value reflects first-run
          cold-start effects (cache warm-up, transient OpenAI API
          latency) rather than steady-state behaviour.
    \item \textbf{Token consumption:} 31{,}413 total
          (20{,}942 input, 10{,}471 output).
\end{itemize}

\noindent Because no task fails, none of Tiers~1--3 of
Algorithm~\ref{alg:failure} is entered.


\subsection{Pheromone State After Run}

On successful completion, the Pheromone Engine applies the
reinforcement branch of Eq.~\ref{eq:pheromone-update} to the two
competitive winners. The resulting committed affordance markers,
exempt from time-based decay per Section~\ref{sec:icnp}, are:

\begin{itemize}[nosep]
    \item \texttt{AffordanceMarker(warehouse-south, warehouse\_matching)}
          -- intensity $1.0$.
    \item \texttt{AffordanceMarker(cost-estimator, deal\_estimation)}
          -- intensity $1.0$.
\end{itemize}

\noindent Non-competitive agents (Call Transcription, Deal Summary,
Client Necessity, Compliance, Proposal Template) do not produce
affordance markers because they are invoked directly without
bidding (Section~\ref{sec:icnp}). These markers bias bid scoring in
subsequent runs through the trust feedback channel of
Eq.~\ref{eq:bid-score}, reinforcing the successful assignments.


\section{Audit and Traceability}

All phases above write structured records into the common audit
stream described in Section~\ref{sec:audit-traceability}, correlated
by the Pipeline and Run IDs given in the Task Normalization
subsection. Because this run completes validated planning, mandatory
human approval, ethics review, and execution without triggering any
Tier~3 escalation, it receives the maximum traceability score for
framework configuration C0.


\chapter{Failure Recovery Trace: C0}
\label{appendix:failure}


This appendix provides a step-by-step trace of a single injected failure 
event under configuration~C0, illustrating how the three-tier recovery 
mechanism defined in Section~\ref{sec:failure-handling} 
(Algorithm~\ref{alg:failure}) activates in practice. The trace 
corresponds to one of the ten C0 injections aggregated in 
Table~\ref{tab:recovery} and is consistent with the median recovery time 
of 55\,s and IQR of 42--85\,s reported there.

\section*{B.1 Failure Injection}
\begin{itemize}[nosep]
    \item \textbf{Configuration}: C0 (full framework)
    \item \textbf{Client}: FreshCo
    \item \textbf{Failure injected at}: warehouse-match execution stage
    \item \textbf{Pipeline ID}: \texttt{f15dade6-b0d1-4f78-8732-d258c8966a18}
    \item \textbf{Timeout budget}: 300\,s
\end{itemize}

\section*{B.2 Recovery Sequence}

The three-tier recovery mechanism activates in the order defined by 
Algorithm~\ref{alg:failure}.

\begin{enumerate}
    \item \textbf{Detection.} The Task Execution Orchestrator detects the
    \texttt{task-failed} event over RabbitMQ and hands control to the
    Retry Middleware.

    \item \textbf{Tier~1 — Bounded Local Retries.} The Retry Middleware
    attempts re-execution under the configured retry policy with
    exponential backoff, up to the default maximum of three attempts.
    This tier is intended to absorb transient faults such as network
    glitches and intermittent timeouts without escalation. In this run,
    all three attempts fail and the failure is propagated to Tier~2.

    \item \textbf{Tier~2 — Adaptive Replanning (with concurrent pheromone
    penalty).} On retry exhaustion, the LLM Strategist
    (Section~\ref{sec:execution}) inspects the current task graph and
    execution state and proposes a graph delta that re-routes the failed
    task to an alternative agent with compatible capabilities. The
    proposed delta is validated against the framework's structural
    invariants, namely acyclicity, dependency preservation, and exactly
    one assignment per task, before being committed to the coordination
    graph store. Concurrently with the Strategist action, the Pheromone
    Engine applies a penalty update (Eq.~\ref{eq:pheromone-update}) to
    the failed agent's \texttt{AffordanceMarker} intensity, biasing all
    future assignments away from that agent. The pheromone penalty is a
    \emph{concurrent action within Tier~2}, not a separate tier.

    \item \textbf{Tier~3 — Structured Escalation (not reached).} Had
    Tier~2 failed to resolve the event, the system would have raised a
    structured escalation record containing the run and plan identifiers,
    a machine-readable failure reason, the severity level derived from
    the plan's risk classification, recent execution summaries, and an
    indication that automated recovery had been exhausted, and would
    have routed it to a human operator through the monitoring and
    alerting channels. Tier~3 was \textbf{not activated} in this run
    because Tier~2 adaptive replanning resolved the failure on the first
    proposed delta.
\end{enumerate}

\section*{B.3 Timing Breakdown}
The total recovery time of 62\,s decomposes into instrumented components 
and uninstrumented residual time. The two components measured directly 
are reported below. The remainder is attributable to Tier~1 retry 
attempts with exponential backoff, the concurrent pheromone penalty 
write, message-broker propagation, and re-execution of the task on the 
alternative agent.

\begin{itemize}[nosep]
    \item \textbf{LLM Strategist call}: 18.0\,s total
    \begin{itemize}[nosep]
        \item Prompt assembly: 1.2\,s
        \item GPT-4o-mini structured-output inference: 16.4\,s
        \item Response parsing: 0.4\,s
    \end{itemize}
    \item \textbf{Graph-delta validation}: 1.8\,s (acyclicity, dependency
    preservation, and single-assignment checks all passed on the first
    proposal)
    \item \textbf{Total recovery time}: 62\,s
\end{itemize}

\section*{B.4 Outcome}
\begin{itemize}[nosep]
    \item \textbf{Recovery successful}: Yes
    \item \textbf{Resolving tier}: Tier~2 (adaptive replanning)
    \item \textbf{Final pipeline status}: \texttt{COMPLETED}, 12/12 tasks
    completed
    \item \textbf{Persistent effect}: pheromone penalty recorded against
    the failed agent's \texttt{AffordanceMarker}, reducing its priority
    in subsequent bidding rounds
\end{itemize}


\chapter{User Perception Survey}
\label{appendix:survey}

This appendix contains the full questionnaire used in the user evaluation study described in section~\ref{sec:qualitative}. The survey was administered via Google Forms. All responses were collected anonymously in compliance with GDPR
guidelines~\cite{eu_gdpr_2016}.

% ---------------------------------------------------------
\section*{GDPR-Compliant Consent}
\addcontentsline{toc}{section}{GDPR-Compliant Consent}

Before beginning the survey, participants were required to confirm each of the following statements:

\begin{enumerate}[label=\alph*)]
  \item I have read and understood the data protection information above.
  \item I understand that my participation is voluntary and I can withdraw at any time.
  \item I consent to my anonymised responses being used for academic research.
  \item I understand that no personally identifiable information will be published.
\end{enumerate}

% ---------------------------------------------------------
\section*{Participant Profile}
\addcontentsline{toc}{section}{Participant Profile}

\begin{enumerate}
  \item \textbf{Which group best describes your profile?}\\
        \emph{Single choice.}
        \begin{itemize}
          \item Domain Expert (logistics, warehouse brokerage, supply chain professional)
          \item Technical Evaluator (software engineer, AI/ML practitioner, researcher)
        \end{itemize}

  \item \textbf{Years of Professional Experience}\\
        \emph{Single choice.}
        \begin{itemize}
          \item Less than 2 years
          \item 2--5 years
          \item 5--10 years
          \item More than 10 years
        \end{itemize}

  \item \textbf{How familiar are you with AI or large language model tools (e.g., ChatGPT, copilots)?}\\
        \emph{Single choice.}
        \begin{itemize}
          \item Not familiar at all
          \item Somewhat familiar (I've used them casually)
          \item Familiar (I use them regularly in my work)
          \item Very familiar (I build or research AI systems)
        \end{itemize}
\end{enumerate}

% ---------------------------------------------------------
\section*{Section~1: Output Quality}
\addcontentsline{toc}{section}{section 1: Output Quality}

\noindent\emph{All items in this section use a 5-point Likert scale:
1\,=\,Strongly Disagree, 2\,=\,Disagree, 3\,=\,Neutral, 4\,=\,Agree, 5\,=\,Strongly Agree.}

\subsubsection*{Scenario 1 --- FreshCo (Cold-Storage, Milan)}

\begin{enumerate}[resume]
  \item The warehouse suggestion matches the client's stated requirements (location, budget, timeline, storage type).
  \item The compliance check identified the correct regulatory and operational constraints.
  \item The generated proposal is professional enough to send to a client as a first draft.
  \item The recommendation includes information I wouldn't have considered on my own.
\end{enumerate}

\subsubsection*{Scenario 2 --- GreenLeaf (Hazmat / Critical Case)}

\begin{enumerate}[resume]
  \item The warehouse suggestion matches the client's stated requirements.
  \item The compliance check identified the correct regulatory and operational constraints.
  \item The generated proposal is professional enough to send to a client as a first draft.
  \item The recommendation includes information I wouldn't have considered on my own.
\end{enumerate}

\subsubsection*{Scenario 3 --- Failure Recovery Case}

\begin{enumerate}[resume]
  \item The system handled the failure scenario in a reasonable way.
  \item The fallback recommendation was still useful despite the primary failure.
\end{enumerate}

\begin{enumerate}[resume]
  \item \textbf{What is missing from these recommendations that you would add manually?}\\
        \emph{Free text.}

  \item \textbf{Would you trust these outputs as a first draft for a real client engagement? Why or why not?}\\
        \emph{Free text.}
\end{enumerate}

% ---------------------------------------------------------
\section*{Section~2: Decision Transparency}
\addcontentsline{toc}{section}{section 2: Decision Transparency}

\noindent\emph{5-point Likert scale (Strongly Disagree -- Strongly Agree).}

\begin{enumerate}[resume]
  \item I can understand why a particular warehouse was selected over alternatives.
  \item The bidding scores make the agent selection process transparent.
  \item The audit trail provides enough information to explain the decision to a client or stakeholder.
  \item If the output contained an error, I could identify where in the pipeline it occurred.
\end{enumerate}

\begin{enumerate}[resume]
  \item \textbf{What information is missing from the decision trace that would improve your understanding?}\\
        \emph{Free text.}
\end{enumerate}

% ---------------------------------------------------------
\section*{Section~3: Framework Value}
\addcontentsline{toc}{section}{section 3: Framework Value}

\noindent\emph{This section was shown only to Technical Evaluators. 5-point Likert scale (Strongly Disagree -- Strongly Agree).}

\begin{enumerate}[resume]
  \item The multi-agent approach adds value over a single monolithic LLM call.
  \item The automatic failure recovery (fallback to next-best agent) is a useful mechanism.
  \item The SHACL constraints provide meaningful safety guarantees for the outputs.
  \item The pheromone-based adaptation is a practical mechanism for dynamic agent selection.
\end{enumerate}

\begin{enumerate}[resume]
  \item \textbf{In what scenarios would you choose this framework over a simpler orchestration tool (e.g.\, LangChain, a single prompt chain)?}\\
        \emph{Free text.}
\end{enumerate}

% ---------------------------------------------------------
\section*{Section~4: Comparative Evaluation}
\addcontentsline{toc}{section}{section 4: Comparative Evaluation}
\label{appendix:survey:comparative}

\noindent\emph{Participants were shown three anonymised outputs (Output~A, Output~B, Output~C) produced by different system configurations.}

\begin{enumerate}[resume]
  \item \textbf{Which output would you trust most for a real client decision?}\\
        \emph{Single choice: Output~A / Output~B / Output~C.}

  \item \textbf{Which output is easiest to audit if something goes wrong?}\\
        \emph{Single choice: Output~A / Output~B / Output~C.}

  \item \textbf{Rank the three outputs by overall quality} (1\,=\,best, 3\,=\,worst).\\
        \emph{Ranking for each: Output~A, Output~B, Output~C.}

  \item \textbf{What made you prefer your top-ranked output?}\\
        \emph{Free text.}
\end{enumerate}

% ---------------------------------------------------------
\section*{Final Thoughts}
\addcontentsline{toc}{section}{Final Thoughts}

\begin{enumerate}[resume]
  \item \textbf{Overall, how would you rate the usefulness of this framework for warehouse brokerage?}\\
        \emph{5-point scale: 1\,=\,Not useful at all, \ldots, 5\,=\,Extremely useful.}

  \item \textbf{Any additional comments, suggestions, or concerns?}\\
        \emph{Free text.}
\end{enumerate}



\chapter{Survey Materials: Comparative Evaluation}
\label{appendix:survey-section4}


\section*{Client Request (identical for all three approaches)}

\begin{itemize}[nosep]
    \item \textbf{FreshCo Logistics} --- Food \& Beverage company
(Maria Rossi, \href{mailto:maria@freshco.eu}{\texttt{maria@freshco.eu}})
requires a cold-storage warehouse in the Milan metropolitan area.
  \item \textbf{Space} ---     15{,}000--25{,}000\,sqft
  \item \textbf{Budget} ---      \EUR{18{,}000}/month (firm)
  \item \textbf{Lease} ---       36 months
  \item \textbf{Move in} ---   June 1, 2026
  \item \textbf{Requirements} --- cold storage, food-grade flooring,
                      temperature monitoring
  \item \textbf{Priority} ---  High
\end{itemize}

\section{Output A}

Based on the requirements, I recommend \textbf{LogisPark Milano Nord} for FreshCo Logistics.

\paragraph{Why this warehouse:}
\begin{itemize}[nosep]
    \item Located in Milan with 22{,}000\,sqft available.
    \item Has cold storage capabilities.
    \item Monthly rent would be approximately \euro16{,}500 (22{,}000 $\times$ \euro0.75/sqft), within the \euro18{,}000 budget.
    \item Modern facility built in 2019 with enhanced security.
    \item 8 dock doors and 28\,ft ceiling height.
\end{itemize}


\paragraph{Draft proposal:}

\begin{quote}
Dear Maria,

We would like to recommend LogisPark Milano Nord for your cold storage needs. The facility offers 22{,}000 square feet with cold storage capabilities at \euro16{,}500 per month, well within your budget of \euro18{,}000. The warehouse was built in 2019 and features enhanced security and 8 dock doors.

The facility can support your June 2026 move-in timeline. We suggest scheduling a site visit to confirm the cold storage temperature range meets your $-25\,^{\circ}\mathrm{C}$ requirement and to assess the dock configuration for your refrigerated truck fleet.

Please let us know if you would like to proceed with a site visit.

Best regards
\end{quote}

\section{Output B}

\subsubsection*{Warehouse Recommendation: FreshCo Logistics}
\textit{Assigned agent: Warehouse Central Europe.}

Analysis of Central European warehouses:

\begin{table}[H]
\centering
\small
\begin{tabular}{@{}llrcrp{4.2cm}@{}}
\toprule
\textbf{Warehouse} & \textbf{City} & \textbf{Sqft} & \textbf{Cold?} & \textbf{Monthly Cost} & \textbf{Match} \\
\midrule
Bavaria Logistics Hub & Munich    & 40{,}000 & No  & \EUR{34{,}000} & \textsc{no} --- no cold storage, wrong city \\
Stuttgart TechCenter  & Stuttgart & 35{,}000 & No  & \EUR{31{,}500} & \textsc{no} --- no cold storage, wrong city \\
PharmaStore Lyon      & Lyon      & 28{,}000 & Yes & \EUR{26{,}600} & \textsc{partial} --- wrong country, over budget \\
Paris CDG Logistics   & Roissy    & 25{,}000 & Yes & \EUR{27{,}500} & \textsc{partial} --- wrong country, over budget \\
\bottomrule
\end{tabular}
\end{table}

\textbf{No warehouses in the Central Europe region match the client's requirements.} The client needs a Milan-area facility, which is outside this agent's coverage area. PharmaStore Lyon and Paris CDG have cold storage but are in France (wrong location) and exceed the \euro18{,}000 budget.

\textbf{Match Score: }0\,/\,100 for location-eligible options.

\textbf{Recommendation:} Unable to provide a suitable recommendation. This request should be routed to the Southern Europe specialist.

\subsection*{Timeline Analysis: FreshCo Logistics}
\textit{Assigned agent: Speed Estimator.}

\begin{table}[H]
\centering
\small
\begin{tabular}{ll}
\hline
\textbf{Milestone} & \textbf{Estimated Date} \\
\hline
Lease signing               & April 14, 2026 \\
Food-grade flooring install & May 15, 2026 \\
Cold storage verification   & May 22, 2026 \\
Operational                 & June 1, 2026 \\
\hline
\end{tabular}
\end{table}

\textit{Assessment:} timeline is feasible with a 6-week lead time. No critical path risks identified.

\subsection*{Compliance Report}

\begin{verbatim}
RESULT: NO VALID PAIRING TO CHECK
The warehouse agent did not produce a viable recommendation.
Compliance check skipped -- no warehouse-client pairing to validate.
\end{verbatim}

\subsection*{Proposal}

\textit{Unable to generate a complete proposal. The assigned warehouse agent could not find a match in its region. Manual intervention required to re-route to the correct regional agent.}

\section{Output C}

\subsection*{Agent Selection Summary}

\subsubsection{Warehouse matching - competitive bidding.}

\begin{table}[H]
\centering
\small
\begin{tabular}{lccccc}
\hline
\textbf{Agent} & \textbf{capMatch} & \textbf{Trust} & \textbf{Cost-effectiveness} & \textbf{Regional-tag bonus} & \textbf{Bid Score} \\
\hline
Warehouse South   & 1.00 & 0.82 & 0.78 & $+$0.28 (Milan region) & \textbf{0.699} \\
Warehouse Central & 1.00 & 0.78 & 0.62 & 0.00                   & 0.339          \\
Warehouse North   & 1.00 & 0.75 & 0.70 & 0.00                   & 0.320          \\
\hline
\end{tabular}
\end{table}

\textit{Winner:} Warehouse South (0.699). 

\subsubsection{Cost vs.\ speed estimator - competitive bidding.}

\begin{table}[H]
\centering
\small
\begin{tabular}{lccccc}
\hline
\textbf{Agent} & \textbf{capMatch} & \textbf{Trust} & \textbf{Cost-effectiveness} & \textbf{Budget-match bonus} & \textbf{Bid Score} \\
\hline
Cost Estimator  & 1.00 & 0.80 & 0.80 & $+$0.10 (firm budget) & \textbf{0.696} \\
Speed Estimator & 1.00 & 0.78 & 0.65 & 0.00                  & 0.320          \\
\hline
\end{tabular}
\end{table}

\textit{Winner:} Cost Estimator (0.696). FreshCo has high priority but
a firm \euro18{,}000/month budget, which aligns with the cost-optimised
strategy.


\subsection*{Warehouse Recommendation: LogisPark Milano Nord}

\begin{table}[H]
\centering
\small
\begin{tabular}{lllc}
\hline
\textbf{Criterion} & \textbf{Requirement} & \textbf{LogisPark Milano Nord} & \textbf{Match} \\
\hline
Location         & Milan metropolitan area & Via Industriale 42, Milan    & \checkmark \\
Available space  & 15{,}000--25{,}000\,sqft & 22{,}000\,sqft               & \checkmark \\
Ceiling height   & $\geq 24$\,ft           & 28\,ft                       & \checkmark \\
Dock doors       & $\geq 6$                & 8                            & \checkmark \\
Cold storage     & Required                & Yes ($-30\,^{\circ}\mathrm{C}$ capable) & \checkmark \\
Monthly cost     & $\leq$ \euro18{,}000    & \euro16{,}500                & \checkmark \\
Security         & ---                     & Enhanced                     & Bonus \\
\hline
\end{tabular}
\end{table}

\textbf{Match Score: 95\,/\,100.}

\paragraph{Strengths:}
\begin{itemize}[nosep]
    \item Cold zone supports $-30\,^{\circ}\mathrm{C}$ (exceeds $-25\,^{\circ}\mathrm{C}$ requirement).
    \item 8{,}000\,sqft dedicated cold area sufficient for frozen goods.
    \item Landlord offers \euro50{,}000 fit-out allowance for food-grade flooring.
\end{itemize}

\paragraph{Gaps:}
\begin{itemize}[nosep]
    \item 8 dock doors vs.\ 12 refrigerated trucks requested - confirm dock scheduling at site visit.
    \item Temperature monitoring system not confirmed in database - verify during site visit.
\end{itemize}

\subsection*{Compliance Report}

\begin{table}[H]
\centering
\small
\begin{tabular}{lll}
\hline
\textbf{Rule} & \textbf{Result} & \textbf{Detail} \\
\hline
COLD\_STORAGE\_REQUIRED      & PASS & LogisPark has cold storage \\
HAZMAT\_REQUIRED             & N/A  & Not required by client \\
HIGH\_SECURITY\_FOR\_CRITICAL & N/A  & Priority is high, not critical \\
SQFT\_WITHIN\_RANGE          & PASS & 22{,}000\,sqft within 15{,}000--25{,}000 \\
BUDGET\_COMPLIANCE           & PASS & \euro16{,}500/mo under \euro18{,}000 budget \\
\hline
\end{tabular}
\end{table}

\textbf{Result: 5/5 checks passed, no violations, no warnings.}

\subsection*{Cost Analysis}

\begin{table}[H]
\centering
\small
\begin{tabular}{lrl}
\hline
\textbf{Cost Component} & \textbf{Estimate} & \textbf{Notes} \\
\hline
Monthly rent                  & \euro16{,}500  & 22{,}000\,sqft $\times$ \euro0.75/sqft \\
Total lease (36 months)       & \euro594{,}000 & \\
Food-grade flooring           & \euro45{,}000  & Offset by landlord allowance \\
Temperature monitoring system & \euro12{,}000  & One-time install \\
Landlord fit-out allowance    & $-$\euro50{,}000 & Negotiated \\
\textbf{Total cost of occupancy} & \textbf{\euro601{,}000} & Over 36-month lease \\
\textit{Effective monthly cost}  & \textbf{\euro16{,}694}  & \euro1{,}306/mo headroom vs.\ budget \\
\hline
\end{tabular}
\end{table}

\subsection*{Client Proposal}

\begin{quote}
Dear Maria,

Following our analysis of your requirements for a cold-storage facility in the Milan area, we are pleased to recommend \textbf{LogisPark Milano Nord} as your primary option.

\textbf{Property summary:}
\begin{itemize}[nosep]
    \item Location: Via Industriale 42, Milan (Lombardy).
    \item Available space: 22{,}000\,sqft with 28\,ft ceiling clearance.
    \item Cold storage: $-30\,^{\circ}\mathrm{C}$ capable zone (8{,}000\,sqft), exceeding your $-25\,^{\circ}\mathrm{C}$ requirement.
    \item Dock access: 8 dock doors with enhanced security.
    \item Monthly rent: \euro16{,}500 (within your \euro18{,}000 budget).
\end{itemize}

\textbf{Financial terms:}
\begin{itemize}[nosep]
    \item 36-month lease: \euro594{,}000 total.
    \item Landlord-funded fit-out allowance: \euro50{,}000 (covers food-grade flooring upgrade).
    \item Estimated out-of-pocket setup: \euro7{,}000 (temperature monitoring system).
    \item Effective monthly cost: \euro16{,}694.
\end{itemize}

\textbf{Recommended next steps:}
\begin{enumerate}[nosep]
    \item Site visit (available week of March 31).
    \item Confirm dock door scheduling for 12 refrigerated trucks.
    \item Temperature monitoring system specification review.
\end{enumerate}

We are confident this property meets your operational needs while remaining within budget. Please let us know your availability for a site visit.

Best regards, \\
\textit{[Brokerage Name]}
\end{quote}

\subsection*{Decision Trace}

\begin{verbatim}
Client call (Maria Rossi, Feb 20)
   |
   v
Call Transcription  ->  extracts: cold storage, -25 C, 12 trucks,
                                  18K budget, 3-yr lease
   |
   v
Deal Summary  ->  aggregates context: deal stage = negotiation,
                  -30 C capability confirmed via email
   |
   +------------------------------+
   |                              |
   v                              v
Warehouse South (0.699)    Cost Estimator (0.696)
LogisPark, 95/100          EUR 601K / 36 mo,
                           EUR 1,306/mo headroom
   |                              |
   v                              |
Compliance Check                  |
5/5 rules passed                  |
   |                              |
   +------------------------------+
   |
   v
Proposal Template  ->  generates client proposal

All bids, scores, compliance checks, and cost calculations are
stored in the audit log for post-hoc inspection. Any step can
be replayed or explained on demand.
\end{verbatim}

\end{appendices}
