namespace VrageCage.Core;

public sealed class SeekController : IController
{
    public Control Sample(Scenario scenario, VehicleState vehicle, int tick)
    {
        var deltaX = scenario.Goal.Position.X - vehicle.X;
        var deltaZ = scenario.Goal.Position.Z - vehicle.Z;
        var distance = Math.Sqrt(deltaX * deltaX + deltaZ * deltaZ);
        var desired = Math.Atan2(deltaZ, deltaX);
        var error = Normalize(desired - vehicle.HeadingRadians);
        var steering = Math.Clamp(error
            / Math.Max(0.25, scenario.Vehicle.TurnRateRadiansPerSecond * 0.75), -1, 1);

        // Use the kinematic stopping envelope to request a lower target speed as
        // the vehicle approaches the goal. The simulation's acceleration model
        // then ramps down instead of continuing at full throttle until arrival.
        var remaining = Math.Max(0, distance - scenario.Goal.Tolerance);
        var stoppingLimitedSpeed = Math.Sqrt(2 * scenario.Vehicle.Acceleration * remaining);
        var alignment = Math.Clamp(Math.Cos(error), 0, 1);
        var targetSpeed = Math.Min(scenario.Vehicle.MaximumSpeed, stoppingLimitedSpeed)
            * alignment;
        var throttle = targetSpeed / scenario.Vehicle.MaximumSpeed;
        return new Control(throttle, steering);
    }

    private static double Normalize(double angle)
    {
        while (angle > Math.PI) angle -= Math.PI * 2;
        while (angle < -Math.PI) angle += Math.PI * 2;
        return angle;
    }
}
