export default async () => {
  return {
    "shell.env": async (_input, output) => {
      output.env = output.env ?? {}
      if (!output.env["BEADS_ACTOR"]) {
        output.env["BEADS_ACTOR"] = "opencode"
      }
    },
  }
}
