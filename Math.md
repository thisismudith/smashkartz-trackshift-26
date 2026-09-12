# TrackShift Formula Compendium: Validity and Derivations

This document compiles and explains the core mathematical and physical principles underpinning the TrackShift E-Delta system. It aims to provide clear, technically sound derivations and references, ensuring that each formula is understood in its context.

---

## 1. Strategic State Representation (Section 2)

The system operates on a causal strategic state vector $s_k$ at track segment $k$:

$$
s_k =
\begin{bmatrix}
k & E_k & T_k & g_k & \Delta v_k & \dot{g}_k & \epsilon_k & b_k & r_k & u_k
\end{bmatrix}
$$

where:

- $k$: Current track segment.
- $E_k$: Available electrical energy.
- $T_k$: Modeled tyre-performance state.
- $g_k$: Gap to the rival.
- $\Delta v_k$: Relative speed to the rival.
- $\dot{g}_k$: Rate of change of the gap (gap closure/opening rate).
- $\epsilon_k$: Overtake eligibility state.
- $b_k$: Belief over rival tactical state.
- $r_k$: Applicable regulation/power-limit state.
- $u_k$: Decision-relevant uncertainty.

**Causality:** This state must only include information available at or before the decision point, preventing the use of future outcomes as inputs.

---

## 2. Energy Shadow Price (Section 3)

The central strategic quantity is the energy shadow price $\lambda_E$, which estimates the marginal value of additional electrical energy in a given state.

$$
\lambda_E(k,e,T,g,\Delta v,\dot{g},\epsilon,b,r)
=
\frac{
V(k,e+\Delta e,T,g,\Delta v,\dot{g},\epsilon,b,r)
-
V(k,e,T,g,\Delta v,\dot{g},\epsilon,b,r)
}{
\Delta e
}
$$


- $V(s)$ represents the **value function**, quantifying the expected future reward (e.g., probability of being ahead at the horizon) from state $s$.
- The formula is a **finite difference approximation of the partial derivative** of the value function with respect to energy ($\frac{\partial V}{\partial E}$). This is a core concept in optimization and control theory.

**Derivation & Reference:**

This is a direct application of the concept of marginal utility or the shadow price in economic optimization and optimal control theory. The value function $V$ is typically derived through dynamic programming.

- **Reference:** Bertsekas, D.P., *Dynamic Programming and Optimal Control*, Vol. 1 (2005). Carmona & Ludkovski, *Valuation of Energy Storage: An Optimal Switching Approach* (2010) similarly use shadow prices for energy storage valuation.

---

## 3. Gap Dynamics (Section 4)

### 3.1 Distance Gap

Let $x_A(t)$ be the position of the car ahead and $x_O(t)$ be our car’s position at time $t$.

The distance gap $g_d(t)$ is:

$$
g_d(t) = x_A(t) - x_O(t)
$$

The rate of change of this gap is:

$$
\dot{g}_d = \frac{d g_d}{dt} = \frac{dx_A}{dt} - \frac{dx_O}{dt} = v_A - v_O
$$

where $v_A$ and $v_O$ are the velocities of the rival and our car, respectively.

### 3.2 Relative Speed and Closing Rate

The contract defines relative speed $\Delta v$ as:

$$
\Delta v = v_O - v_A
$$

Therefore, $\dot{g}_d = -\Delta v$. The **closing rate** is the speed at which the gap is decreasing, which is the positive version of the gap’s rate of change:

$$
\text{closing rate} = -\dot{g}_d = v_O - v_A = \Delta v
$$

A positive closing rate ($\Delta v > 0$) means our car is faster and closing the gap.

**Reference:** Standard kinematics and relative motion principles from introductory physics.

---

## 4. Gap Projection to Detection Line (Section 5)

Assuming constant speeds over the distance $L$ to the Detection Line:

$$
g_{d,D} = g_d + L\left(\frac{v_A}{v_O} - 1\right)
$$

Where:

- $g_{d,D}$ is the projected distance gap at the Detection Line.
- $g_d$ is the current distance gap.
- $L$ is the distance remaining to the Detection Line.
- $v_A$ and $v_O$ are rival and our car speeds.

**Derivation:**

1. Time for our car to reach the Detection Line: $t_O = L / v_O$.
2. In this time, the rival travels an additional distance: $d_A = v_A \cdot t_O = v_A \cdot (L / v_O)$.
3. The new gap at the Detection Line is the current gap plus the difference in distance covered by the rival relative to our car over distance $L$: $g_{d,D} = g_d + d_A - L = g_d + (v_A L / v_O) - L$.

**More Advanced Projection:**

A more robust model integrates speed profiles over the segment:

$$
g_{d,D} = g_d + \int_{0}^{t_D} \left[v_A(\tau) - v_O(\tau)\right]\,d\tau
$$

This accounts for non-constant speeds due to acceleration, braking, and energy deployment.

**Reference:** Principles of kinematics and motion integration. Traffic engineering models for gap prediction often use similar physics-based projections.

---

## 5. Overtake Eligibility (Section 6)

### 5.1 Eligibility Margin

The **eligibility margin** $m_{\text{elig}}$ quantifies how far within the regulatory gap threshold a car is.

$$
m_{\text{elig}} = g_{\text{threshold}} - g_{\text{detection}}
$$

- $g_{\text{threshold}}$: The maximum allowed gap at the Detection Line (defined by regulations).
- $g_{\text{detection}}$: The actual or predicted gap at the Detection Line.
- If $m_{\text{elig}} > 0$, the car is potentially eligible.

### 5.2 Probabilistic Eligibility

When the predicted gap $\mu_D$ has uncertainty $\sigma_D$, modeled as a normal distribution $G_D \sim \mathcal{N}(\mu_D, \sigma_D^2)$, the probability of eligibility is:

$$
P_{\text{eligible}} = P(G_D \leq g_{\text{threshold}}) = \Phi\left(\frac{g_{\text{threshold}} - \mu_D}{\sigma_D}\right)
$$

where $\Phi$ is the cumulative distribution function (CDF) of the standard normal distribution.

**Reference:** Statistical modeling of decision-making under uncertainty. Gap acceptance theory in traffic flow (e.g., TRB publications like the one from 1967 on “GAP ACCEPTANCE IN THE FREEWAY MERGING PROCESS”) uses probabilistic methods for critical gaps.

---

## 6. Energy Required to Unlock Eligibility (Section 22)

This estimates the additional deployable energy $\Delta E_{\text{unlock}}$ needed to make Overtake eligibility likely ($P_{\text{eligible}} \geq p^\star$).

Using a first-order approximation for the gap’s dependence on energy ($g_D(E)$):

$$
\Delta E_{\text{unlock}} \approx \frac{g_D(E) - g_{\text{threshold}}}{-\frac{d g_D}{dE}}
$$

This formula assumes that more deployment ($E$) reduces the gap ($g_D$), hence $\frac{dg_D}{dE} < 0$.

The probabilistic version involves solving for $\Delta E$ such that $P(\text{eligible} \mid E + \Delta E) \geq p^\star$.

**Reference:** Principles of sensitivity analysis and optimization. Applied in contexts where resource allocation affects outcomes, such as energy economics and control systems.

---

## 7. Speed-Dependent Electrical Power Envelope (Section 8, 20.1)

The maximum electrical power $P_{\text{max}}$ is a function of speed $v$, mode $m$, and event rules $r$: $P_{\text{max}}(v,m,r)$.

### 7.1 Piecewise Linear Interpolation

For a given mode (e.g., ‘NORMAL’ or ‘OVERRIDE’), the envelope is defined by breakpoints $(v_i, P_i)$. For speed $v$ between $v_i$ and $v_{i+1}$:

$$
P_{\max}(v) = P_i + \frac{P_{i+1} - P_i}{v_{i+1} - v_i}(v - v_i)
$$

This can be expressed using linear interpolation parameter $\alpha$:

$$
\alpha = \frac{v - v_i}{v_{i+1} - v_i}, \quad P_{\max}(v) = (1 - \alpha)P_i + \alpha P_{i+1}
$$

### 7.2 Regulatory Basis for 2026

The 2026 FIA Formula 1 Technical Regulations specify speed-dependent power limits. For instance, the *Power Unit Technical Regulations* mention:

- **OVERRIDE Mode:** $P(\text{kW}) = 7100 - 20 \times \text{car speed (kph)}$ for speeds below 355 kph, and $P=0$ at or above 355 kph.
- **Other Limits:** $P(\text{kW}) = 1850 - 5 \times \text{car speed (kph)}$ below 340 kph.

**Derivation & Reference:**

These formulas are directly from the FIA regulations. The implementation uses piecewise linear interpolation to represent these curves accurately.

- **Reference:** FIA 2026 Formula 1 Technical Regulations (specifically the Power Unit sections). Example document: [https://api.fia.com/sites/default/files/fia\_2026\_formula\_1\_technical\_regulations\_pu\_-](https://api.fia.com/sites/default/files/fia_2026_formula_1_technical_regulations_pu_-_issue_4_-_2023-10-25.pdf)[*issue\_4*](https://api.fia.com/sites/default/files/fia_2026_formula_1_technical_regulations_pu_-_issue_4_-_2023-10-25.pdf)[-\_2023-10-25.pdf](https://api.fia.com/sites/default/files/fia_2026_formula_1_technical_regulations_pu_-_issue_4_-_2023-10-25.pdf) (See Article 5.4.7 and related clauses).

---

## 8. Energy Conversion (Section 9)

Energy $\Delta E$ is derived from power $P$ over a time interval $\Delta t$:

$$
\Delta E = \int P(t)\,dt
$$

For discrete intervals:

$$
\Delta E \approx P \Delta t
$$

Unit conversions:

- $1\,\text{kW} \times 1\,\text{s} = 1\,\text{kJ}$
- $1\,\text{MJ} = 1000\,\text{kJ}$

**Derivation:** Fundamental definition of power as the rate of energy transfer. $P = dE/dt$.

**Reference:** Standard physics textbooks (e.g., Halliday & Resnick, *Fundamentals of Physics*). Electric vehicle engineering resources often detail these conversions.

---

## 9. Energy Store Dynamics (Section 10)

The change in stored electrical energy $E$ over a time step $\Delta t$ is given by:

$$
E_{k+1} = E_k + \eta_h P_{\text{harvest},k} \Delta t - \frac{P_{\text{deploy},k} \Delta t}{\eta_d}
$$

where:

- $E_k$: Stored energy at step $k$.
- $P_{\text{harvest},k}$: Recovered electrical power.
- $P_{\text{deploy},k}$: Deployed electrical power.
- $\eta_h$: Harvesting efficiency.
- $\eta_d$: Deployment efficiency.

**Derivation:** This is an energy balance equation, accounting for energy input (harvesting) and output (deployment), with physical efficiencies reducing the net transfer.

**Reference:** Battery modeling and electric vehicle powertrain analysis. Chalal et al., “Development of a Digital Twin for an Electric Vehicle Emulator” (2023) provides a similar model.

---

## 10. Dynamic Programming & Bellman Equation (Section 31)

The core of the planner uses dynamic programming, specifically the **Bellman optimality equation**, to determine the optimal value of states and actions. For a finite horizon $k$:

$$
V_k(s) = \max_{a \in A_{legal}(s)} \mathbb{E}[R(s,a,\xi) + V_{k+1}(F(s,a,\xi))]
$$

- $V_k(s)$: The maximum expected cumulative reward (value) achievable from state $s$ at time step $k$.
- $A_{legal}(s)$: The set of actions legally available in state $s$.
- $a$: The chosen action.
- $R(s,a,\xi)$: The immediate reward received after taking action $a$ in state $s$, potentially influenced by random outcome $\xi$.
- $F(s,a,\xi)$: The next state resulting from action $a$ and random outcome $\xi$.
- $\mathbb{E}[\cdot]$: The expectation taken over possible random outcomes $\xi$.

**Derivation:** The Bellman principle states that an optimal policy must be optimal for all subsequent decisions, regardless of the path taken to reach the current state. This equation recursively defines the optimal value function by considering all possible actions and their expected future outcomes.

**Reference:**

- Richard S. Sutton and Andrew G. Barto, *Reinforcement Learning: An Introduction* (2nd ed., 2018), Chapter 4.
- Dimitri P. Bertsekas, *Dynamic Programming and Stochastic Control* (2019).
- Lecture notes on stochastic dynamic programming often show the derivation from discrete-time Bellman equations or through Hamilton-Jacobi-Bellman (HJB) equations in continuous time.

---

## 11. Vehicle Longitudinal Dynamics (Section 28)

The energy twin estimates electrical contribution using a longitudinal power balance. Forces acting on the car are balanced by tractive force $F_{\text{traction}}$:

$$
F_{\text{traction}} = M \frac{dv}{dt} + F_{\text{drag}} + F_{\text{roll}} + F_{\text{gradient}}
$$

Where:

- $M$: Vehicle mass.
- $dv/dt$: Acceleration.
- $F_{\text{drag}}$: Aerodynamic drag force ( $\approx \frac{1}{2} \rho C_d A_f v^2$ ).
- $F_{\text{roll}}$: Rolling resistance force ( $\approx c_r M g \cos(\theta)$ ).
- $F_{\text{gradient}}$: Gradient force ( $= M g \sin(\theta)$ ).

The tractive power $P_{\text{traction}}$ is $F_{\text{traction}} \cdot v$. The electrical power $P_K$ (from MGU-K) is then related by drivetrain and ICE efficiencies ($\eta_d, \eta_{ICE}$):

$$
P_K \approx \frac{P_{\text{traction}}}{\eta_d} - P_{\text{ICE},\text{effective}}
$$

where $P_{\text{ICE},\text{effective}}$ is the effective power contribution from the internal combustion engine.

**Reference:** Standard vehicle dynamics textbooks and engineering resources.

- Delft University eCARS slides: [https://delftxdownloads.tudelft.nl/ECARS2x\_Electric\_Cars\_Technology/Module\_1/eCARS2x\_2019\_T1-1\_Sizing\_of\_the\_EV\_powertrain-slides.pdf](https://delftxdownloads.tudelft.nl/ECARS2x_Electric_Cars_Technology/Module_1/eCARS2x_2019_T1-1_Sizing_of_the_EV_powertrain-slides.pdf)
- SAE International publications on vehicle dynamics.

---

# Validity of Formulas and Project Approach

The formulas and concepts used in your project are generally **valid and well-grounded** in their respective scientific and engineering domains.

1. **Foundational Physics and Mathematics:** The core equations for kinematics ($\dot{g}$, $\Delta v$), energy ($P = dE/dt$), and vehicle dynamics ($F_{\text{traction}} = ...$) are standard and have been validated over centuries of physics research.
2. **Control Theory and Optimization:** The use of the Bellman equation for dynamic programming, shadow prices for marginal value, and probabilistic modeling for uncertainty are established techniques in operations research, economics, and control engineering.
3. **Regulatory Interpretation:** 
   - **Technical Regulations (Power Envelope):** The system correctly identifies and aims to implement the speed-dependent power limits for the MGU-K as specified in the 2026 FIA Technical Regulations. The search found explicit regulatory text confirming these speed-dependent limits and modes, validating this core aspect of the project.
   - **Sporting Regulations (Overtake Mechanics):** While the detailed sporting regulations (provided in the search) cover general race control, session formats, and penalties, they do not explicitly define terms like “Detection Line” or “Activation Line” with precise numerical values for gaps or distances. It appears that the project’s “Detection Line” and “Activation Line” are **operational definitions** derived from the *intent* of the regulations regarding overtaking zones and eligibility criteria, rather than direct citations of specific line definitions within the sporting regulations themselves. The **principle** of a speed-dependent power envelope and the concept of overtaking eligibility based on car performance and race context are directly supported by the technical regulations. The specific thresholds and lines would be defined in event-specific configurations, which the project correctly plans to incorporate.
4. **Energy Management:** The model for energy store dynamics, including efficiencies and budget constraints, is standard for hybrid and electric vehicle energy management systems.
5. **Data Handling and Modeling:** The emphasis on provenance, leakage prevention, feature engineering, and robust evaluation (ablation studies, hold-out validation) aligns with best practices in machine learning and data science for complex systems.

**Overall Validity:**

The project’s approach to combining physics-based modeling, dynamic programming, and regulatory constraints is sound. The validity lies not just in the formulas themselves, but in their correct application within a causal, uncertainty-aware framework that respects regulatory boundaries and the complexities of F1 racing strategy. The project’s contract clearly outlines this disciplined approach, which is crucial for building a robust and accurate system.