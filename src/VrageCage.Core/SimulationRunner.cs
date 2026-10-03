using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace VrageCage.Core;

public sealed class SimulationRunner
{
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) }
    };

    public RunResult Run(Scenario scenario, IController controller)
    {
        var validation = Validate(scenario);
        if (validation is not null)
            return Invalid(scenario, validation);

        var frames = new List<TelemetryFrame>(scenario.MaximumTicks + 1);
        var spec = scenario.Vehicle;
        var state = new VehicleState(spec.Position.X, spec.Position.Z,
            spec.HeadingRadians, 0);
        var dt = scenario.TickMilliseconds / 1000.0;

        for (var tick = 0; tick <= scenario.MaximumTicks; tick++)
        {
            var distance = Distance(state.X, state.Z,
                scenario.Goal.Position.X, scenario.Goal.Position.Z);
            if (distance <= scenario.Goal.Tolerance)
                return Finish(scenario, frames, state, distance, RunOutcome.ReachedGoal, tick);
            if (Collides(scenario, state))
                return Finish(scenario, frames, state, distance, RunOutcome.Collision, tick);
            if (tick == scenario.MaximumTicks)
                return Finish(scenario, frames, state, distance, RunOutcome.TimedOut, tick);

            Control control;
            try
            {
                control = controller.Sample(scenario, state, tick);
            }
            catch (Exception exception)
            {
                return Finish(scenario, frames, state, distance, RunOutcome.ControllerFault,
                    tick, exception.GetType().Name);
            }
            if (!Finite(control.Throttle, control.Steering))
                return Finish(scenario, frames, state, distance, RunOutcome.ControllerFault,
                    tick, "non-finite control");
            control = new Control(Math.Clamp(control.Throttle, -1, 1),
                Math.Clamp(control.Steering, -1, 1));
            frames.Add(new TelemetryFrame(tick, tick * dt, state, control, distance, "running", null));

            var heading = Normalize(state.HeadingRadians
                + control.Steering * spec.TurnRateRadiansPerSecond * dt);
            var targetSpeed = control.Throttle * spec.MaximumSpeed;
            var speedDelta = Math.Clamp(targetSpeed - state.Speed,
                -spec.Acceleration * dt, spec.Acceleration * dt);
            var speed = state.Speed + speedDelta;
            var next = new VehicleState(
                state.X + Math.Cos(heading) * speed * dt,
                state.Z + Math.Sin(heading) * speed * dt,
                heading, speed);
            if (SweptCollides(scenario, state, next))
            {
                var nextDistance = Distance(next.X, next.Z,
                    scenario.Goal.Position.X, scenario.Goal.Position.Z);
                return Finish(scenario, frames, next, nextDistance, RunOutcome.Collision, tick + 1);
            }
            state = next;
        }

        throw new InvalidOperationException("bounded loop terminated unexpectedly");
    }

    private static string? Validate(Scenario scenario)
    {
        if (scenario.Schema != "vragecage.scenario/v1") return "unsupported schema";
        if (string.IsNullOrWhiteSpace(scenario.Id)) return "missing id";
        if (scenario.Vehicle is null || scenario.Vehicle.Position is null
            || scenario.Goal is null || scenario.Goal.Position is null
            || scenario.Obstacles is null) return "missing required object";
        if (scenario.TickMilliseconds is < 1 or > 1000) return "invalid tick";
        if (scenario.MaximumTicks is < 1 or > 1_000_000) return "invalid budget";
        if (!Finite(scenario.Vehicle.Position.X, scenario.Vehicle.Position.Z,
                scenario.Vehicle.HeadingRadians, scenario.Vehicle.Radius,
                scenario.Vehicle.MaximumSpeed, scenario.Vehicle.Acceleration,
                scenario.Vehicle.TurnRateRadiansPerSecond,
                scenario.Goal.Position.X, scenario.Goal.Position.Z, scenario.Goal.Tolerance))
            return "non-finite value";
        if (scenario.Vehicle.Radius <= 0 || scenario.Vehicle.MaximumSpeed <= 0
            || scenario.Vehicle.Acceleration <= 0
            || scenario.Vehicle.TurnRateRadiansPerSecond <= 0) return "invalid vehicle";
        if (scenario.Goal.Tolerance <= 0) return "invalid goal";
        if (scenario.Obstacles.Any(obstacle => string.IsNullOrWhiteSpace(obstacle.Id)
            || obstacle.Radius <= 0
            || !Finite(obstacle.Center.X, obstacle.Center.Z, obstacle.Radius)))
            return "invalid obstacle";
        if (scenario.Obstacles.Select(obstacle => obstacle.Id).Distinct(StringComparer.Ordinal).Count()
            != scenario.Obstacles.Count) return "duplicate obstacle id";
        return null;
    }

    private static RunResult Invalid(Scenario scenario, string reason)
    {
        var scenarioHash = Hash(JsonSerializer.Serialize(scenario, Json));
        var traceHash = Hash(reason);
        return new RunResult(new RunReceipt(scenario.Id, scenarioHash, traceHash,
            RunOutcome.InvalidScenario, 0, 0, reason), Array.Empty<TelemetryFrame>());
    }

    private static RunResult Finish(Scenario scenario, List<TelemetryFrame> frames,
        VehicleState state, double goalDistance, RunOutcome outcome, int tick,
        string? detail = null)
    {
        frames.Add(new TelemetryFrame(tick, tick * scenario.TickMilliseconds / 1000.0,
            state, new Control(0, 0), goalDistance, OutcomeName(outcome), detail));
        var scenarioJson = JsonSerializer.Serialize(scenario, Json);
        var trace = string.Join('\n', frames.Select(frame => JsonSerializer.Serialize(frame, Json)));
        return new RunResult(new RunReceipt(scenario.Id, Hash(scenarioJson), Hash(trace),
            outcome, tick, frames.Count, detail), frames.AsReadOnly());
    }

    private static bool Collides(Scenario scenario, VehicleState state) =>
        scenario.Obstacles.Any(obstacle => Distance(state.X, state.Z,
            obstacle.Center.X, obstacle.Center.Z) <= scenario.Vehicle.Radius + obstacle.Radius);

    private static bool SweptCollides(Scenario scenario, VehicleState from, VehicleState to) =>
        scenario.Obstacles.Any(obstacle => SegmentDistanceSquared(from.X, from.Z, to.X, to.Z,
            obstacle.Center.X, obstacle.Center.Z)
            <= Math.Pow(scenario.Vehicle.Radius + obstacle.Radius, 2));

    private static double SegmentDistanceSquared(double ax, double az, double bx, double bz,
        double px, double pz)
    {
        var dx = bx - ax;
        var dz = bz - az;
        var lengthSquared = dx * dx + dz * dz;
        var t = lengthSquared <= 0 ? 0 : Math.Clamp(((px - ax) * dx + (pz - az) * dz)
            / lengthSquared, 0, 1);
        var x = ax + t * dx;
        var z = az + t * dz;
        return (px - x) * (px - x) + (pz - z) * (pz - z);
    }

    private static double Distance(double ax, double az, double bx, double bz) =>
        Math.Sqrt((ax - bx) * (ax - bx) + (az - bz) * (az - bz));

    private static double Normalize(double angle)
    {
        while (angle > Math.PI) angle -= Math.PI * 2;
        while (angle < -Math.PI) angle += Math.PI * 2;
        return angle;
    }

    private static bool Finite(params double[] values) => values.All(double.IsFinite);

    private static string Hash(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    private static string OutcomeName(RunOutcome outcome) => outcome switch
    {
        RunOutcome.ReachedGoal => "reached_goal",
        RunOutcome.Collision => "collision",
        RunOutcome.TimedOut => "timed_out",
        RunOutcome.ControllerFault => "controller_fault",
        _ => "invalid_scenario"
    };
}
