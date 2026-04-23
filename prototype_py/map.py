# Extend positions with contemporary global leaders
from matplotlib import pyplot as plt


positions_global = {
    "You": (2, -3),
    "J. Sai Deepak": (2, -3.5),
    "Anand Ranganathan": (3, -1),
    "Mahatma Gandhi": (-2, 2),
    "Jawaharlal Nehru": (-2, -1),
    "Winston Churchill": (2, -2),
    "Adolf Hitler": (3, -5),
    "Karl Marx": (-4, 3),
    "Vladimir Lenin": (-4, -3),
    "Joseph Stalin": (-4, -5),
    # Contemporary leaders (approximate placements)
    "Narendra Modi": (2.5, -2.5),   # nationalist conservative, pro-market reforms
    "Joe Biden": (-1, 0),           # centrist-left economically, socially moderate-liberal
    "Xi Jinping": (-3, -4),         # authoritarian left (state socialism with strong control)
    "Vladimir Putin": (3, -4),      # authoritarian right, nationalist, corporatist
    "Emmanuel Macron": (1, 1),      # centrist, liberal economically, socially libertarian
}

# Create plot
plt.figure(figsize=(10,10))
plt.axhline(0, color="black")
plt.axvline(0, color="black")

# Quadrant labels
plt.text(-4.5,4.5,"Libertarian Left", fontsize=9, ha="center", va="center")
plt.text(4.5,4.5,"Libertarian Right", fontsize=9, ha="center", va="center")
plt.text(-4.5,-4.5,"Authoritarian Left", fontsize=9, ha="center", va="center")
plt.text(4.5,-4.5,"Authoritarian Right", fontsize=9, ha="center", va="center")

# Plot positions with distinctive markers
markers = {
    "You": ("red", "*"),
    "J. Sai Deepak": ("blue", "s"),
    "Anand Ranganathan": ("green", "^"),
    "Mahatma Gandhi": ("orange", "o"),
    "Jawaharlal Nehru": ("purple", "D"),
    "Winston Churchill": ("brown", "P"),
    "Adolf Hitler": ("black", "X"),
    "Karl Marx": ("cyan", "h"),
    "Vladimir Lenin": ("magenta", "<"),
    "Joseph Stalin": ("gray", ">"),
    "Narendra Modi": ("darkred", "s"),
    "Joe Biden": ("skyblue", "o"),
    "Xi Jinping": ("darkgreen", "p"),
    "Vladimir Putin": ("darkorange", "^"),
    "Emmanuel Macron": ("gold", "d"),
}

for label, (x,y) in positions_global.items():
    color, marker = markers[label]
    size = 140 if label == "You" else 100
    plt.scatter(x, y, color=color, s=size, marker=marker, label=label)

plt.legend(loc="upper left", bbox_to_anchor=(1,1))
plt.xlim(-5,5)
plt.ylim(-5,5)
plt.xlabel("Economic (Left ←   → Right)")
plt.ylabel("Social (Libertarian ↑   ↓ Authoritarian)")
plt.title("Comparison: You vs Historical & Contemporary Global Leaders")

plt.grid(True, linestyle="--", alpha=0.6)
plt.show()
