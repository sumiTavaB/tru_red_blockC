import matplotlib.pyplot as plt
import numpy as np

# Data extracted from the attached files, sorted by Alpha Value
alpha_values = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])

# 1. Committed Transaction Count (TXs)
tx_count_data = np.array([1946, 1928, 1756, 1081, 1433, 1391, 1240, 1532, 1391, 1107])

# 2. Average Latency (seconds)
latency_data = np.array([2.3727, 2.6327, 2.3082, 1.7183, 2.1814, 2.0448, 1.8660, 2.3651, 2.4177, 2.0934])

# 3. Transactions Per Second (TPS)
tps_data = np.array([4.481, 5.183, 5.352, 5.022, 4.921, 4.908, 5.247, 4.963, 4.346, 4.480])

# 4. Standard Deviation of Verifier Values
std_dev_data = np.array([4614.03, 3184.06, 185.11, 36.13, 34.38, 25.98, 23.97, 43.20, 26.27, 21.02])

# --- Create figure and primary y-axis ---
fig, ax1 = plt.subplots(figsize=(10, 6))
fig.suptitle('Network Performance vs. Alpha Value (Mid Network, 200 Rounds)', fontsize=14)

# Left y-axis (Committed TXs and TPS)
l1 = ax1.plot(alpha_values, tx_count_data, marker='o', color='b', linestyle='-', label='Committed TXs')
l2 = ax1.plot(alpha_values, tps_data, marker='^', color='g', linestyle='-.', label='TPS')

ax1.set_xlabel('Alpha Value ($\\alpha$)')
ax1.set_ylabel('Committed TXs / TPS')
ax1.tick_params(axis='y', labelcolor='b')
ax1.grid(True, linestyle='--', alpha=0.6)

# --- Right y-axis for latency and std deviation ---
ax2 = ax1.twinx()
l3 = ax2.plot(alpha_values, latency_data, marker='s', color='r', linestyle='--', label='Latency (s)')
l4 = ax2.plot(alpha_values, std_dev_data, marker='d', color='m', linestyle=':', label='Std Dev of Values')

ax2.set_ylabel('Latency (s) / Std Dev')
ax2.tick_params(axis='y', labelcolor='r')

# --- Combine legends from both axes ---
lines = l1 + l2 + l3 + l4
labels = [line.get_label() for line in lines]
ax1.legend(lines, labels, loc='upper right')

# --- Adjust layout and save ---
plt.tight_layout(rect=[0, 0.03, 1, 0.95])
plt.savefig('alpha_trends_dual_yaxis.png')
# plt.show()  # Uncomment when running locally
