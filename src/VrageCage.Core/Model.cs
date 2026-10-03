namespace VrageCage.Core;

public sealed record Point2(double X, double Z);
public sealed record CircleObstacle(string Id, Point2 Center, double Radius);
public sealed record VehicleSpec(Point2 Position, double HeadingRadians, double Radius,
    double MaximumSpeed, double Acceleration, double TurnRateRadiansPerSecond);
public sealed record GoalSpec(Point2 Position, double Tolerance);
public sealed record Scenario(string Schema, string Id, long Seed, int TickMilliseconds,
    int MaximumTicks, VehicleSpec Vehicle, GoalSpec Goal, IReadOnlyList<CircleObstacle> Obstacles);

public readonly record struct Control(double Throttle, double Steering);
public readonly record struct VehicleState(double X, double Z, double HeadingRadians, double Speed);
public sealed record TelemetryFrame(int Tick, double TimeSeconds, VehicleState Vehicle,
    Control Control, double GoalDistance, string State, string? Detail);

public enum RunOutcome { ReachedGoal, Collision, TimedOut, InvalidScenario, ControllerFault }

public sealed record RunReceipt(string ScenarioId, string ScenarioSha256, string TraceSha256,
    RunOutcome Outcome, int CompletedTick, int FrameCount, string? Detail);

public sealed record RunResult(RunReceipt Receipt, IReadOnlyList<TelemetryFrame> Frames);

public interface IController
{
    Control Sample(Scenario scenario, VehicleState vehicle, int tick);
}
