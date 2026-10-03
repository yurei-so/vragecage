using VrageCage.Core;

var passed = 0;
Run("straight scenario reaches goal", StraightScenarioReachesGoal);
Run("replay is byte-for-byte deterministic", ReplayIsDeterministic);
Run("collision terminates run", CollisionTerminatesRun);
Run("budget produces timeout", BudgetProducesTimeout);
Run("invalid scenario fails closed", InvalidScenarioFailsClosed);
Run("controller output is bounded", ControllerOutputIsBounded);
Run("terminal outcome is part of trace", TerminalOutcomeIsPartOfTrace);
Run("heading-aware oracle preserves lower-cost arrival", HeadingAwareOraclePreservesArrival);
Run("swept collision cannot tunnel through obstacle", SweptCollisionPreventsTunneling);
Run("non-finite input fails closed", NonFiniteInputFailsClosed);
Run("non-finite controller output becomes receipt", NonFiniteControlBecomesReceipt);
Run("controller exception becomes receipt", ControllerExceptionBecomesReceipt);
Run("random scenarios remain bounded and deterministic", RandomScenariosAreBoundedAndDeterministic);
Console.WriteLine($"VRageCage core: {passed}/13 checks passed.");
return;

void StraightScenarioReachesGoal()
{
    var result = new SimulationRunner().Run(Scenario(), new SeekController());
    Check(result.Receipt.Outcome == RunOutcome.ReachedGoal, "goal was not reached");
    Check(result.Frames.Count > 0, "trace was empty");
    Check(result.Receipt.FrameCount == result.Frames.Count, "receipt frame count disagrees");
}

void ReplayIsDeterministic()
{
    var runner = new SimulationRunner();
    var first = runner.Run(Scenario(), new SeekController());
    var second = runner.Run(Scenario(), new SeekController());
    Check(first.Receipt == second.Receipt, "receipts differ");
    Check(first.Frames.SequenceEqual(second.Frames), "frames differ");
}

void CollisionTerminatesRun()
{
    var scenario = Scenario() with
    {
        Obstacles = new[] { new CircleObstacle("wall", new Point2(4, 0), 1.5) }
    };
    var result = new SimulationRunner().Run(scenario, new SeekController());
    Check(result.Receipt.Outcome == RunOutcome.Collision, "collision was not detected");
    Check(result.Receipt.CompletedTick < scenario.MaximumTicks, "collision did not stop promptly");
}

void BudgetProducesTimeout()
{
    var scenario = Scenario() with { MaximumTicks = 2 };
    var result = new SimulationRunner().Run(scenario, new SeekController());
    Check(result.Receipt.Outcome == RunOutcome.TimedOut, "budget was not enforced");
    Check(result.Receipt.CompletedTick == 2, "timeout tick is inaccurate");
}

void InvalidScenarioFailsClosed()
{
    var scenario = Scenario() with { TickMilliseconds = 0 };
    var result = new SimulationRunner().Run(scenario, new SeekController());
    Check(result.Receipt.Outcome == RunOutcome.InvalidScenario, "invalid scenario ran");
    Check(result.Frames.Count == 0, "invalid scenario emitted frames");
}

void ControllerOutputIsBounded()
{
    var result = new SimulationRunner().Run(Scenario(), new WildController());
    Check(result.Frames.All(frame => Math.Abs(frame.Control.Throttle) <= 1
        && Math.Abs(frame.Control.Steering) <= 1), "unbounded controller output escaped");
}

void TerminalOutcomeIsPartOfTrace()
{
    var result = new SimulationRunner().Run(Scenario(), new SeekController());
    Check(result.Frames[^1].State == "reached_goal", "terminal frame is missing");
    Check(result.Frames[^1].Control == new Control(0, 0), "terminal actuator state is not neutral");
}

void HeadingAwareOraclePreservesArrival()
{
    var blocked = new bool[7, 7];
    blocked[3, 0] = true;
    blocked[4, 1] = true;
    blocked[6, 2] = true;
    blocked[3, 3] = true;
    blocked[4, 3] = true;
    blocked[6, 4] = true;
    blocked[1, 5] = true;
    blocked[5, 5] = true;
    var cost = HeadingAwareGridOracle.FindCost(blocked, (0, 3), (6, 3), 3);
    Check(cost == 17, "oracle lost the lower-cost arrival heading");
}

void SweptCollisionPreventsTunneling()
{
    var scenario = Scenario() with
    {
        TickMilliseconds = 1000,
        Vehicle = Scenario().Vehicle with { MaximumSpeed = 100, Acceleration = 100 },
        Obstacles = new[] { new CircleObstacle("needle", new Point2(50, 0), 0.01) },
        Goal = new GoalSpec(new Point2(200, 0), 0.1)
    };
    var result = new SimulationRunner().Run(scenario, new SeekController());
    Check(result.Receipt.Outcome == RunOutcome.Collision, "vehicle tunneled through obstacle");
}

void NonFiniteInputFailsClosed()
{
    var scenario = Scenario() with
    {
        Vehicle = Scenario().Vehicle with { MaximumSpeed = double.PositiveInfinity }
    };
    var result = new SimulationRunner().Run(scenario, new SeekController());
    Check(result.Receipt.Outcome == RunOutcome.InvalidScenario, "non-finite input ran");
}

void NonFiniteControlBecomesReceipt()
{
    var result = new SimulationRunner().Run(Scenario(), new NonFiniteController());
    Check(result.Receipt.Outcome == RunOutcome.ControllerFault, "NaN escaped controller boundary");
    Check(result.Receipt.Detail == "non-finite control", "fault detail is inaccurate");
}

void ControllerExceptionBecomesReceipt()
{
    var result = new SimulationRunner().Run(Scenario(), new ThrowingController());
    Check(result.Receipt.Outcome == RunOutcome.ControllerFault, "controller exception escaped runner");
    Check(result.Receipt.Detail == nameof(InvalidOperationException), "exception detail is inaccurate");
    Check(result.Frames[^1].Detail == nameof(InvalidOperationException), "trace lost exception detail");
}

void RandomScenariosAreBoundedAndDeterministic()
{
    var random = new Random(8826);
    var runner = new SimulationRunner();
    for (var index = 0; index < 2_000; index++)
    {
        var scenario = Scenario() with
        {
            Id = "fuzz-" + index,
            Seed = index,
            MaximumTicks = random.Next(1, 200),
            TickMilliseconds = random.Next(1, 501),
            Vehicle = Scenario().Vehicle with
            {
                Position = new Point2(random.NextDouble() * 20 - 10,
                    random.NextDouble() * 20 - 10),
                HeadingRadians = random.NextDouble() * Math.PI * 2 - Math.PI
            },
            Goal = new GoalSpec(new Point2(random.NextDouble() * 20 - 10,
                random.NextDouble() * 20 - 10), 0.25),
            Obstacles = Array.Empty<CircleObstacle>()
        };
        var first = runner.Run(scenario, new SeekController());
        var second = runner.Run(scenario, new SeekController());
        Check(first.Receipt == second.Receipt, "fuzz receipt changed on replay");
        Check(first.Frames.SequenceEqual(second.Frames), "fuzz trace changed on replay");
        Check(first.Receipt.CompletedTick <= scenario.MaximumTicks, "fuzz run exceeded budget");
        Check(first.Frames.All(frame => double.IsFinite(frame.Vehicle.X)
            && double.IsFinite(frame.Vehicle.Z) && double.IsFinite(frame.GoalDistance)),
            "fuzz run emitted non-finite telemetry");
    }
}

Scenario Scenario() => new(
    "vragecage.scenario/v1", "flat-seek", 8826, 100, 500,
    new VehicleSpec(new Point2(0, 0), 0, 0.5, 5, 3, 1.5),
    new GoalSpec(new Point2(10, 0), 0.5),
    Array.Empty<CircleObstacle>());

void Run(string name, Action test)
{
    test();
    passed++;
    Console.WriteLine("PASS " + name);
}

void Check(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
}

sealed class WildController : IController
{
    public Control Sample(Scenario scenario, VehicleState vehicle, int tick) => new(99, -99);
}

sealed class NonFiniteController : IController
{
    public Control Sample(Scenario scenario, VehicleState vehicle, int tick) => new(double.NaN, 0);
}

sealed class ThrowingController : IController
{
    public Control Sample(Scenario scenario, VehicleState vehicle, int tick) =>
        throw new InvalidOperationException("expected test fault");
}
