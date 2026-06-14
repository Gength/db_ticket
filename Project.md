# Project Specification: DB Weekend Sparpreis Ticket Scanner (WSL with WSLg)

## 1. Project Overview
A Python-based automation tool deployed in WSL2 (Ubuntu) leveraging WSLg for visual rendering to scan Deutsche Bahn (DB) promotional weekend tickets ("Sparpreis"). The system runs periodically on weekends, bypasses bot detection using browser automation, filters results based on strict criteria, and triggers an email notification via QQ Mail SMTP upon finding valid tickets or optimized fallback recommendations.

## 2. Core Functional Requirements

### 2.1 Configuration Inputs
The Agent must implement a `config.toml` or `config.json` file containing the following strict structures:
* `routes`: A list of route objects. Each contains:
    * `from_station`: string (e.g., "Berlin Hbf")
    * `to_station`: string (e.g., "München Hbf")
    * `trip_type`: string ["SINGLE", "ROUND_TRIP"]
* `search_window`:
    * `start_date`: YYYY-MM-DD or relative string (e.g., "today")
    * `end_date`: YYYY-MM-DD or relative string (e.g., "today+30")
* `passenger`:
    * `age`: integer (Crucial for DB age-specific discounts)
    * `bahncard`: string ["none", "25_2nd", "50_2nd", "25_1st", "50_1st"]
* `filters`:
    * `target_price`: float (Maximum threshold in EUR)
    * `ticket_class`: string ["1st", "2nd", "ANY"]
    * `direct_only`: boolean
    * `max_transfers`: integer (Ignored if direct_only is true)
    * `min_transfer_time`: integer (Minutes required to change trains)

### 2.2 Task Scheduling & Execution Modes
* **Production Mode (Headless)**: Scheduled via Linux `cron` expression (`0 2 * * 6,0` - Saturday and Sunday at 02:00 AM CET). Runs implicitly inside a virtual display environment or native headless browser wrapper.
* **Debug/Visual Mode (Headed)**: Controlled via the environment variable `DEBUG_VISUAL=true`. When executed manually in a WSLg-enabled terminal, it spawns a visible Chromium instance on the Windows desktop for debugging cookie policies and selectors.

### 2.3 Data Scraping & Browser Automation Strategy
* **Target Engine**: Playwright (Python async API).
* **WSLg Display Integration**: The script must dynamically check for the `$DISPLAY` environment variable to ensure seamless visual rendering through X11/Wayland backends provided by WSLg when running in headed mode.
* **Anti-Bot Strategy**: Implement anti-fingerprinting by steering Playwright via a persistent context to retain consent cookies, rotating viewport boundaries, and disabling the automation flag (`navigator.webdriver`).

## 3. Business Logic & Filtering Rules (Strict Priority)

When processing returned train schedules, the Agent must filter and sort data using the following hierarchy:

1. **Price Cap Filter**: Identify tickets where $\text{Price} \le \text{target\_price}$.
2. **Direct Route Priority**: 
    * If `direct_only` is True, instantly discard connections with transfers ($\text{transfers} > 0$).
    * If `direct_only` is False, sort available connections by:
        $$\text{Score} = w_1 \cdot \text{is\_direct} + w_2 \cdot \left(\frac{\text{target\_price} - \text{price}}{\text{target\_price}}\right)$$
        *(Where Direct Trains take absolute precedence; secondarily sorted by lowest price)*.
3. **Transfer Safety**: If transfers are allowed, connections with transfer times less than `min_transfer_time` (default 15 mins for DB delay buffer) must be excluded.
4. **Age-Based Pricing Request**:
   * The Agent must map the input `passenger.age` to the correct DB traveler category API parameters.
   * If `15 <= age <= 26`, the query must explicitly target "Young" ticket tiers (e.g., Super Sparpreis Young).
   * If `age >= 65`, the query must explicitly target "Senior" ticket tiers.
5. **Travel Class Filtering**:
   * **1st**: Fetch 1st-class tickets. Discard all 2nd-class results.
   * **2nd**: Fetch 2nd-class tickets. Discard all 1st-class results.
   * **ANY**: Fetch both classes. If both fall below `target_price`, prioritize the 1st-class option if the price delta is $\le 10\%$, otherwise select the lowest absolute price.
6. **Fallback Mechanism (No-Match Guard)**:
   * If **zero** connections satisfy $\text{Price} \le \text{target\_price}$, the Agent must not silent-fail. It must compute the following **"Additional Recommendations" (保底建议)**:
     * **Recommendation A (Cheapest Over-Budget)**: Find the absolute lowest price connection available within the search window, regardless of whether it exceeds `target_price`.
     * **Recommendation B (Best Alternative Route)**: If `direct_only` was set to True, relax this restriction and find the cheapest transfer connection ($\text{transfers} \ge 1$) that respects the `min_transfer_time`.

## 4. Notification & Logging Requirements

### 4.1 SMTP Integration
* The system must utilize Python's `smtplib.SMTP_SSL` via QQ Mail SMTP (Port 465) using an App Password.
* **Notification Payload Variants**:
  * **Standard Match**: Email subject must be `[DB Alert] Match Found!`. The body contains a Markdown table of qualified tickets.
  * **Fallback Notification**: Email subject must be `[DB Info] No Matches - Alternative Recommendations`. The body must clearly list **Recommendation A** and **Recommendation B** with a prominent notice: *"No tickets found under your target price of {target_price} EUR. Here are the closest alternatives."*

### 4.2 State Management & Anti-Spam
* Maintain a local JSON cache (`history.json`) to track triggered notifications.
* **Rule**: Do not send an alert if the exact same train connection at the same or higher price was successfully notified within the last 48 hours (applies to both Standard and Fallback notifications).

## 5. Technical Stack Constraints
* **Environment**: WSL2 (Ubuntu 22.04/24.04 LTS) with WSLg enabled.
* **Package Manager**: `uv` (Astral)
* **Core Libraries**: `playwright`, `pyyaml` or `toml`, `pydantic`
* **Language**: Python 3.12+
* **Code Documentation**: All comments and docstrings within the codebase must be written in English exclusively.