namespace VrageCage.Core;

public static class HeadingAwareGridOracle
{
    private readonly record struct State(int X, int Z, int Dx, int Dz, bool HasHeading);
    private static readonly (int X, int Z)[] Directions =
    {
        (1, 0), (-1, 0), (0, 1), (0, -1)
    };

    public static double? FindCost(bool[,] blocked, (int X, int Z) start,
        (int X, int Z) goal, double turnPenalty)
    {
        ArgumentNullException.ThrowIfNull(blocked);
        if (turnPenalty < 0) throw new ArgumentOutOfRangeException(nameof(turnPenalty));
        if (!Contains(blocked, start.X, start.Z) || !Contains(blocked, goal.X, goal.Z)
            || blocked[start.X, start.Z] || blocked[goal.X, goal.Z]) return null;

        var initial = new State(start.X, start.Z, 0, 0, false);
        var costs = new Dictionary<State, double> { [initial] = 0 };
        var frontier = new PriorityQueue<State, double>();
        frontier.Enqueue(initial, 0);

        while (frontier.TryDequeue(out var state, out var queuedCost))
        {
            if (queuedCost > costs[state] + 0.000001) continue;
            if (state.X == goal.X && state.Z == goal.Z) return queuedCost;

            foreach (var direction in Directions)
            {
                var x = state.X + direction.X;
                var z = state.Z + direction.Z;
                if (!Contains(blocked, x, z) || blocked[x, z]) continue;
                var turned = state.HasHeading && (state.Dx != direction.X || state.Dz != direction.Z);
                var candidate = queuedCost + 1 + (turned ? turnPenalty : 0);
                var next = new State(x, z, direction.X, direction.Z, true);
                if (costs.TryGetValue(next, out var known) && candidate >= known - 0.000001)
                    continue;
                costs[next] = candidate;
                frontier.Enqueue(next, candidate);
            }
        }

        return null;
    }

    private static bool Contains(bool[,] blocked, int x, int z) =>
        x >= 0 && z >= 0 && x < blocked.GetLength(0) && z < blocked.GetLength(1);
}
