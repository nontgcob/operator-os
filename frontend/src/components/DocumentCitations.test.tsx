import { fireEvent, render, screen } from "@testing-library/react";

import { DocumentCitations } from "@/components/DocumentCitations";

describe("DocumentCitations", () => {
  it("shows a safe text preview on hover without embedding the PDF", () => {
    const { container } = render(
      <DocumentCitations
        citations={[
          {
            citation_id: "citation-1",
            document_id: "manual-id",
            filename: "manual.pdf",
            page: 4,
            section: "Component Introduction",
            excerpt: "The display and controls are located on the front panel.",
          },
        ]}
      />
    );

    const source = screen.getByRole("link");
    expect(container.querySelector("iframe")).not.toBeInTheDocument();

    fireEvent.mouseEnter(source.closest("li")!);

    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "The display and controls are located on the front panel."
    );
    expect(container.querySelector("iframe")).not.toBeInTheDocument();
    expect(source).toHaveAttribute(
      "href",
      "http://localhost:8000/documents/manual-id/file#page=4&view=FitH"
    );
  });
});
