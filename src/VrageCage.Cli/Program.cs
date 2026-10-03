using System.Text.Json;
using System.Text.Json.Serialization;
using VrageCage.Core;

if (args.Length != 1)
{
    Console.Error.WriteLine("usage: vragecage <scenario.json>");
    return 2;
}

var options = new JsonSerializerOptions
{
    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    PropertyNameCaseInsensitive = true,
    Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) }
};
var scenario = JsonSerializer.Deserialize<Scenario>(File.ReadAllText(args[0]), options)
    ?? throw new InvalidDataException("scenario is empty");
var result = new SimulationRunner().Run(scenario, new SeekController());
foreach (var frame in result.Frames)
    Console.WriteLine(JsonSerializer.Serialize(new { type = "frame", value = frame }, options));
Console.WriteLine(JsonSerializer.Serialize(new { type = "receipt", value = result.Receipt }, options));
return result.Receipt.Outcome == RunOutcome.ReachedGoal ? 0 : 1;
